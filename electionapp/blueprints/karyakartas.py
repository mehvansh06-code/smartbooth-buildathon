"""
karyakartas.py
Karyakarta & Panna Pramukh Management & WhatsApp Dispatch Blueprint:
- /api/karyakartas, /api/karyakartas/list         — List karyakartas with progress metrics
- /api/karyakartas/assign                        — Assign voters to karyakarta
- /api/karyakartas/unassign                      — Unassign voters from karyakarta
- /api/karyakartas/add                           — Create or update karyakarta
- /api/karyakartas/delete                        — Delete karyakarta
- /api/karyakartas/<int:k_id>/voters             — Get voters assigned to karyakarta
- /api/karyakarta/my_voters                      — Get current karyakarta's assigned voters
- /api/karyakartas/<int:k_id>/send_whatsapp      — Dispatch voter assignment list via Meta WhatsApp
"""

import os
import re
import secrets
import logging
import psycopg2.extras
from flask import Blueprint, jsonify, request, session, current_app, redirect

from electionapp.audit import audit_action
from electionapp.config import load_tenant_config, get_cached_community_tags
from electionapp.tenancy import resolve_tenant
from electionapp.blueprints.auth import operator_or_super_admin_required
from electionapp import whatsapp_quota
import meta_whatsapp

logger = logging.getLogger(__name__)
karyakartas_bp = Blueprint('karyakartas', __name__)


def ensure_karyakarta_schema(conn):
    """Ensures karyakartas table exists and has access_token, is_active, and audit columns,
    and backfills tokens for any existing records lacking one.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS karyakartas (
                    karyakarta_id SERIAL PRIMARY KEY,
                    tenant_id INT NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    phone VARCHAR(16) NOT NULL,
                    role VARCHAR(32) NOT NULL,
                    part_no INT NOT NULL,
                    page_no INT,
                    assigned_voter_count INT DEFAULT 0,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    access_token VARCHAR(64) UNIQUE,
                    is_active BOOLEAN DEFAULT TRUE,
                    last_accessed_at TIMESTAMPTZ,
                    token_rotated_at TIMESTAMPTZ
                );
                ALTER TABLE karyakartas ADD COLUMN IF NOT EXISTS access_token VARCHAR(64) UNIQUE;
                ALTER TABLE karyakartas ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;
                ALTER TABLE karyakartas ADD COLUMN IF NOT EXISTS last_accessed_at TIMESTAMPTZ;
                ALTER TABLE karyakartas ADD COLUMN IF NOT EXISTS token_rotated_at TIMESTAMPTZ;
                CREATE INDEX IF NOT EXISTS idx_karyakartas_token ON karyakartas(access_token);
                CREATE INDEX IF NOT EXISTS idx_karyakartas_tenant ON karyakartas(tenant_id);
            """)
            cur.execute("SELECT karyakarta_id FROM karyakartas WHERE access_token IS NULL;")
            missing = cur.fetchall()
            for r in missing:
                kid = r[0] if isinstance(r, (list, tuple)) else r['karyakarta_id']
                tok = secrets.token_urlsafe(24)
                cur.execute("UPDATE karyakartas SET access_token = %s, is_active = COALESCE(is_active, TRUE) WHERE karyakarta_id = %s", (tok, kid))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Could not auto-ensure karyakarta schema: %s", e)


def sync_subaccount_users_to_karyakartas(conn, tid):
    """
    Synchronizes any user in `users` table with role IN ('karyakarta', 'booth_agent')
    for this tenant into `karyakartas` table so they seamlessly appear in worker
    assignment dropdowns and can be assigned voters.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, full_name, phone, assigned_booth, assigned_booths, role, access_token
                FROM users
                WHERE tenant_id = %s
                  AND role IN ('karyakarta', 'booth_agent')
                  AND is_active = TRUE
            """, (tid,))
            sub_users = cur.fetchall()
            for u in sub_users:
                uid = u[0]
                name = (u[1] or '').strip()
                phone = (u[2] or '').strip()
                booth = u[3]
                if booth is None and u[4] and len(u[4]) > 0:
                    booth = u[4][0]
                booth_no = int(booth) if booth is not None else 0
                role = u[5] or 'karyakarta'
                tok = u[6] or secrets.token_urlsafe(24)

                if not name:
                    continue

                cur.execute("""
                    SELECT karyakarta_id FROM karyakartas
                    WHERE tenant_id = %s AND (access_token = %s OR (name = %s AND phone = %s AND phone <> ''))
                    LIMIT 1
                """, (tid, tok, name, phone))
                k_row = cur.fetchone()
                if k_row:
                    k_id = k_row[0]
                    cur.execute("UPDATE users SET karyakarta_id = %s WHERE user_id = %s AND (karyakarta_id IS NULL OR karyakarta_id <> %s)", (k_id, uid, k_id))
                else:
                    cur.execute("""
                        INSERT INTO karyakartas (tenant_id, name, phone, part_no, role, access_token, is_active, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, TRUE, NOW())
                        RETURNING karyakarta_id
                    """, (tid, name, phone, booth_no, role, tok))
                    new_kid = cur.fetchone()[0]
                    cur.execute("UPDATE users SET karyakarta_id = %s WHERE user_id = %s", (new_kid, uid))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.warning("Could not sync subaccount users to karyakartas: %s", e)


def _get_tenant():
    """Resolve active tenant slug and repo using authoritative tenancy resolver."""
    return resolve_tenant()


# ==============================================================================
# KARYAKARTA & PANNA PRAMUKH MANAGEMENT ROUTES
# ==============================================================================

@karyakartas_bp.route('/api/karyakartas', methods=['GET'])
@karyakartas_bp.route('/api/karyakartas/list', methods=['GET'])
@karyakartas_bp.route('/api/karyakartas/options', methods=['GET'])
def api_karyakartas_list():
    force_auth = os.environ.get('FORCE_AUTH_CHECK') == '1'
    is_testing = (
        not force_auth
        and (current_app.config.get('TESTING', False) or os.environ.get('FLASK_TESTING') == '1')
    )
    if not is_testing and not session.get('user_id'):
        if request.is_json or request.path.startswith('/api/'):
            return jsonify({'success': False, 'error': 'लॉगिन आवश्यक है (Authentication required)'}), 401
        return redirect('/login')

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    if not hasattr(repo, 'get_connection'):
        return jsonify({"success": True, "karyakartas": [], "total": 0})

    raw_ward = request.args.get('ward_no') or request.args.get('ward') or request.args.get('wards')
    raw_part = request.args.get('part_no') or request.args.get('part') or request.args.get('parts') or request.args.get('booth')
    from electionapp.config import parse_location_filter_extended
    ward_no, part_no, part_nos, gp, ward_nos = parse_location_filter_extended(raw_ward, raw_part)

    target_wards = []
    if ward_no is not None:
        target_wards.append(ward_no)
    elif ward_nos:
        target_wards.extend(ward_nos)

    target_parts = []
    if part_no is not None:
        target_parts.append(part_no)
    elif part_nos:
        target_parts.extend(part_nos)

    v_join_conditions = [
        "v.tenant_id = k.tenant_id",
        "v.assigned_karyakarta_id = k.karyakarta_id",
        "LOWER(COALESCE(v.status, '')) <> 'deleted'"
    ]
    v2_conditions = [
        "v2.tenant_id = k.tenant_id",
        "v2.assigned_karyakarta_id = k.karyakarta_id",
        "LOWER(COALESCE(v2.status, '')) <> 'deleted'"
    ]
    k_direct_conditions = []
    k_where_conditions = ["k.tenant_id = %(tid)s"]
    sql_params = {'tid': tid}

    if target_wards:
        w_clause = "(v.ward_no = ANY(%(target_wards)s) OR (COALESCE(v.ward_no, 0) = 0 AND v.part_no = ANY(%(target_wards)s)))"
        w2_clause = "(v2.ward_no = ANY(%(target_wards)s) OR (COALESCE(v2.ward_no, 0) = 0 AND v2.part_no = ANY(%(target_wards)s)))"
        v_join_conditions.append(w_clause)
        v2_conditions.append(w2_clause)
        k_direct_conditions.append("k.part_no = ANY(%(target_wards)s)")
        sql_params['target_wards'] = target_wards

    if target_parts:
        v_join_conditions.append("v.part_no = ANY(%(target_parts)s)")
        v2_conditions.append("v2.part_no = ANY(%(target_parts)s)")
        k_direct_conditions.append("k.part_no = ANY(%(target_parts)s)")
        sql_params['target_parts'] = target_parts

    if gp:
        v_join_conditions.append("LOWER(TRIM(v.gram_panchayat)) = LOWER(TRIM(%(gp)s))")
        v2_conditions.append("LOWER(TRIM(v2.gram_panchayat)) = LOWER(TRIM(%(gp)s))")
        sql_params['gp'] = gp

    if target_wards or target_parts or gp:
        exists_clause = f"EXISTS (SELECT 1 FROM voters v2 WHERE {' AND '.join(v2_conditions)})"
        if k_direct_conditions:
            k_where_conditions.append(f"({' OR '.join(k_direct_conditions)} OR {exists_clause})")
        else:
            k_where_conditions.append(exists_clause)

    v_join_sql = " AND ".join(v_join_conditions)
    k_where_sql = " AND ".join(k_where_conditions)

    with repo.get_connection() as conn:
        ensure_karyakarta_schema(conn)
        sync_subaccount_users_to_karyakartas(conn, tid)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT 
                    k.karyakarta_id as id,
                    k.name,
                    k.phone,
                    COALESCE(k.part_no, 0) as booth_no,
                    COALESCE(k.role, '') as area,
                    '' as notes,
                    k.created_at,
                    k.access_token,
                    COALESCE(k.is_active, TRUE) as is_active,
                    k.last_accessed_at,
                    count(v.voter_id) as assigned_voters,
                    sum(case when v.is_voted = true then 1 else 0 end) as voted_count
                FROM karyakartas k
                LEFT JOIN voters v ON {v_join_sql}
                WHERE {k_where_sql}
                GROUP BY k.karyakarta_id, k.name, k.phone, k.part_no, k.role, k.created_at, k.access_token, k.is_active, k.last_accessed_at
                ORDER BY k.part_no, k.name
            """, sql_params)
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                token = d.get('access_token')
                if not token:
                    token = secrets.token_urlsafe(24)
                    try:
                        with conn.cursor() as u_cur:
                            u_cur.execute("UPDATE karyakartas SET access_token = %s WHERE karyakarta_id = %s", (token, d['id']))
                        conn.commit()
                    except Exception:
                        pass
                    d['access_token'] = token

                d['magic_link'] = f"/k/{token}"
                av = int(d.get('assigned_voters') or 0)
                vc = int(d.get('voted_count') or 0)
                d['pending_count'] = av - vc
                d['turnout_pct'] = round((vc / av * 100), 1) if av > 0 else 0.0
                rows.append(d)

    return jsonify({"success": True, "karyakartas": rows, "total": len(rows)})


@karyakartas_bp.route('/api/karyakartas/assign', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.assign', entity_type='karyakarta')
def api_karyakartas_assign():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_ids = data.get('voter_ids', [])
    k_id = data.get('karyakarta_id')

    if not voter_ids or not k_id:
        return jsonify({'success': False, 'error': 'voter_ids and karyakarta_id are required'}), 400

    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return jsonify({'success': False, 'error': 'No valid voter IDs'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT name FROM karyakartas WHERE tenant_id = %s AND karyakarta_id = %s", (tid, int(k_id)))
            k_row = cur.fetchone()
            if not k_row:
                return jsonify({'success': False, 'error': f'Karyakarta {k_id} not found'}), 404
            k_name = k_row['name']

            cur.execute("""
                UPDATE voters
                SET assigned_karyakarta_id = %s,
                    assigned_karyakarta_name = %s
                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
            """, (int(k_id), k_name, tid, clean_ids, clean_ids))
            count = cur.rowcount

            # Update assigned voter count in karyakartas
            cur.execute("""
                UPDATE karyakartas
                SET assigned_voter_count = (
                    SELECT count(*) FROM voters WHERE tenant_id = %s AND assigned_karyakarta_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                )
                WHERE tenant_id = %s AND karyakarta_id = %s
            """, (tid, int(k_id), tid, int(k_id)))
        conn.commit()

    return jsonify({'success': True, 'count': count, 'karyakarta_name': k_name})


@karyakartas_bp.route('/api/karyakartas/unassign', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.unassign', entity_type='karyakarta')
def api_karyakartas_unassign():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_ids = data.get('voter_ids', [])
    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return jsonify({'success': False, 'error': 'No valid voter IDs'}), 400

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET assigned_karyakarta_id = NULL,
                    assigned_karyakarta_name = NULL
                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
            """, (tid, clean_ids, clean_ids))
            count = cur.rowcount
        conn.commit()

    return jsonify({'success': True, 'count': count})


@karyakartas_bp.route('/api/karyakartas/add', methods=['POST'])
@karyakartas_bp.route('/api/karyakartas/save', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.add', entity_type='karyakarta')
def api_karyakartas_add():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    k_id = data.get('id') or data.get('karyakarta_id')
    name = (data.get('name') or '').strip()
    phone = (data.get('phone') or '').strip()
    booth_raw = str(data.get('booth_no') or data.get('part_no') or 0).strip()
    if booth_raw.startswith('ward:'):
        try:
            booth_no = int(booth_raw.split(':', 1)[1])
        except (ValueError, TypeError):
            booth_no = 0
    elif booth_raw.startswith('part:') or booth_raw.startswith('booth:'):
        try:
            booth_no = int(booth_raw.split(':', 1)[1])
        except (ValueError, TypeError):
            booth_no = 0
    else:
        try:
            booth_no = int(booth_raw)
        except (ValueError, TypeError):
            booth_no = 0
    area = (data.get('area') or data.get('role') or '').strip()

    if not tid:
        return jsonify({'success': False, 'error': f"अभियान '{tenant}' मान्य नहीं है (Invalid campaign)"}), 400

    if not name:
        return jsonify({'success': False, 'error': 'कार्यकर्ता का नाम आवश्यक है (Name is required)'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if k_id and str(k_id).isdigit():
                cur.execute("""
                    UPDATE karyakartas
                    SET name = %s, phone = %s, part_no = %s, role = %s
                    WHERE tenant_id = %s AND karyakarta_id = %s
                    RETURNING access_token, is_active
                """, (name, phone, booth_no, area, tid, int(k_id)))
                r = cur.fetchone()
                cur.execute("""
                    UPDATE voters
                    SET assigned_karyakarta_name = %s
                    WHERE tenant_id = %s AND assigned_karyakarta_id = %s
                """, (name, tid, int(k_id)))
                res_id = int(k_id)
                token = r['access_token'] if r and r.get('access_token') else None
                if not token:
                    token = secrets.token_urlsafe(24)
                    cur.execute("UPDATE karyakartas SET access_token = %s WHERE karyakarta_id = %s", (token, res_id))
            else:
                token = secrets.token_urlsafe(24)
                cur.execute("""
                    INSERT INTO karyakartas (tenant_id, name, phone, part_no, role, assigned_voter_count, access_token, is_active, created_at)
                    VALUES (%s, %s, %s, %s, %s, 0, %s, TRUE, NOW())
                    RETURNING karyakarta_id, access_token
                """, (tid, name, phone, booth_no, area, token))
                res = cur.fetchone()
                res_id = res['karyakarta_id']
                token = res.get('access_token', token)
        conn.commit()

    return jsonify({
        'success': True,
        'id': res_id,
        'name': name,
        'phone': phone,
        'access_token': token,
        'magic_link': f"/k/{token}"
    })


@karyakartas_bp.route('/api/karyakartas/<int:k_id>/rotate_token', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.rotate_token', entity_type='karyakarta', entity_id_arg='k_id')
def api_karyakarta_rotate_token(k_id: int):
    """Rotates karyakarta magic login token immediately revoking older links."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    new_token = secrets.token_urlsafe(24)
    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE karyakartas
                SET access_token = %s, token_rotated_at = NOW(), is_active = TRUE
                WHERE tenant_id = %s AND karyakarta_id = %s
                RETURNING karyakarta_id, name, access_token
            """, (new_token, tid, k_id))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Karyakarta {k_id} not found'}), 404
        conn.commit()

    return jsonify({
        'success': True,
        'id': k_id,
        'name': row['name'],
        'access_token': new_token,
        'magic_link': f"/k/{new_token}"
    })


@karyakartas_bp.route('/api/karyakartas/<int:k_id>/toggle_status', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.toggle_status', entity_type='karyakarta', entity_id_arg='k_id')
def api_karyakarta_toggle_status(k_id: int):
    """Toggles active/deactivated state of karyakarta for instant access revocation."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                UPDATE karyakartas
                SET is_active = NOT COALESCE(is_active, TRUE)
                WHERE tenant_id = %s AND karyakarta_id = %s
                RETURNING karyakarta_id, name, is_active
            """, (tid, k_id))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Karyakarta {k_id} not found'}), 404
        conn.commit()

    return jsonify({
        'success': True,
        'id': k_id,
        'name': row['name'],
        'is_active': bool(row['is_active'])
    })


@karyakartas_bp.route('/api/karyakartas/delete', methods=['POST'])
@karyakartas_bp.route('/api/karyakartas/delete/<int:k_id>', methods=['POST'])
@operator_or_super_admin_required
@audit_action('karyakarta.delete', entity_type='karyakarta', entity_id_arg='k_id')
def api_karyakartas_delete(k_id=None):
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(silent=True) or {}
    if not k_id:
        k_id = data.get('id') or data.get('karyakarta_id')
    if not k_id or not str(k_id).isdigit():
        return jsonify({'success': False, 'error': 'Invalid karyakarta ID'}), 400

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET assigned_karyakarta_id = NULL,
                    assigned_karyakarta_name = NULL
                WHERE tenant_id = %s AND assigned_karyakarta_id = %s
            """, (tid, int(k_id)))
            cur.execute("DELETE FROM karyakartas WHERE tenant_id = %s AND karyakarta_id = %s", (tid, int(k_id)))
        conn.commit()

    return jsonify({'success': True, 'deleted_id': int(k_id)})


@karyakartas_bp.route('/api/karyakartas/<int:k_id>/voters', methods=['GET'])
@operator_or_super_admin_required
def api_karyakarta_voters(k_id):
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    raw_ward = request.args.get('ward_no') or request.args.get('ward') or request.args.get('wards')
    raw_part = request.args.get('part_no') or request.args.get('part') or request.args.get('parts') or request.args.get('booth')
    from electionapp.config import parse_location_filter_extended
    ward_no, part_no, part_nos, gp, ward_nos = parse_location_filter_extended(raw_ward, raw_part)

    target_wards = []
    if ward_no is not None:
        target_wards.append(ward_no)
    elif ward_nos:
        target_wards.extend(ward_nos)

    target_parts = []
    if part_no is not None:
        target_parts.append(part_no)
    elif part_nos:
        target_parts.extend(part_nos)

    where_clauses = [
        "tenant_id = %(tid)s",
        "assigned_karyakarta_id = %(k_id)s",
        "LOWER(COALESCE(status, '')) <> 'deleted'"
    ]
    params = {'tid': tid, 'k_id': k_id}

    if target_wards:
        where_clauses.append("(ward_no = ANY(%(target_wards)s) OR (COALESCE(ward_no, 0) = 0 AND part_no = ANY(%(target_wards)s)))")
        params['target_wards'] = target_wards

    if target_parts:
        where_clauses.append("part_no = ANY(%(target_parts)s)")
        params['target_parts'] = target_parts

    if gp:
        where_clauses.append("LOWER(TRIM(gram_panchayat)) = LOWER(TRIM(%(gp)s))")
        params['gp'] = gp

    where_sql = " AND ".join(where_clauses)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT karyakarta_id as id, name, phone, part_no as booth_no, role as area FROM karyakartas WHERE tenant_id = %s AND karyakarta_id = %s", (tid, k_id))
            k_info = cur.fetchone()
            if not k_info:
                return jsonify({'success': False, 'error': 'Karyakarta not found'}), 404

            cur.execute(f"""
                SELECT 
                    voter_id as id,
                    ward_no,
                    part_no,
                    sl_no,
                    COALESCE(name_hi, name_en) as name,
                    COALESCE(rel_name_hi, rel_name_en) as rel_name,
                    epic_no,
                    COALESCE(house_no, '') as house_no,
                    COALESCE(contact_phone, '') as phone,
                    is_voted,
                    voter_category
                FROM voters
                WHERE {where_sql}
                ORDER BY part_no, sl_no
            """, params)
            voters = [dict(r) for r in cur.fetchall()]

    return jsonify({'success': True, 'karyakarta': dict(k_info), 'voters': voters, 'total': len(voters)})


@karyakartas_bp.route('/api/karyakarta/my_voters', methods=['GET'])
def api_karyakarta_my_voters():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    # Prevent IDOR: Field karyakartas can only view their own assigned electors
    if session.get('role') == 'karyakarta':
        user_karya_id = session.get('karyakarta_id')
        if not user_karya_id:
            return jsonify({'success': False, 'error': 'सत्र समाप्त या अमान्य (Unauthorized karyakarta session)'}), 403
    else:
        user_karya_id = request.args.get('karyakarta_id', type=int) or session.get('karyakarta_id')

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if not user_karya_id:
                # Default to first karyakarta for demo/testing
                cur.execute("SELECT karyakarta_id FROM karyakartas WHERE tenant_id = %s ORDER BY karyakarta_id LIMIT 1", (tid,))
                k_row = cur.fetchone()
                user_karya_id = k_row['karyakarta_id'] if k_row else 1

            cur.execute("""
                SELECT 
                    voter_id as id,
                    ward_no,
                    part_no,
                    sl_no,
                    sl_no as serial_no,
                    name_hi,
                    name_en,
                    COALESCE(name_hi, name_en) as name,
                    rel_name_hi as relative_name_hi,
                    rel_name_en as relative_name_en,
                    COALESCE(rel_name_hi, rel_name_en) as rel_name,
                    age,
                    gender,
                    family_id,
                    epic_no,
                    COALESCE(house_no, '') as house_no,
                    COALESCE(contact_phone, '') as phone,
                    COALESCE(contact_phone, '') as contact_phone,
                    is_voted,
                    voter_category,
                    COALESCE(caste_tag, '') as caste_tag,
                    COALESCE(notes, '') as notes,
                    is_outside
                FROM voters
                WHERE tenant_id = %s AND assigned_karyakarta_id = %s
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
                ORDER BY part_no, sl_no
            """, (tid, user_karya_id))
            voters = [dict(r) for r in cur.fetchall()]

            # Fallback for booth agents / operators ONLY if not a field karyakarta
            if not voters and user_karya_id and session.get('role') != 'karyakarta':
                cur.execute("SELECT part_no FROM karyakartas WHERE tenant_id = %s AND karyakarta_id = %s", (tid, user_karya_id))
                k_info = cur.fetchone()
                if k_info and k_info.get('part_no'):
                    cur.execute("""
                        SELECT 
                            voter_id as id,
                            part_no,
                            sl_no,
                            sl_no as serial_no,
                            name_hi,
                            name_en,
                            COALESCE(name_hi, name_en) as name,
                            rel_name_hi as relative_name_hi,
                            rel_name_en as relative_name_en,
                            COALESCE(rel_name_hi, rel_name_en) as rel_name,
                            age,
                            gender,
                            family_id,
                            epic_no,
                            COALESCE(house_no, '') as house_no,
                            COALESCE(contact_phone, '') as phone,
                            COALESCE(contact_phone, '') as contact_phone,
                            is_voted,
                            voter_category,
                            COALESCE(caste_tag, '') as caste_tag,
                            COALESCE(notes, '') as notes,
                            is_outside
                        FROM voters
                        WHERE tenant_id = %s AND part_no = %s
                        ORDER BY sl_no
                        LIMIT 500
                    """, (tid, k_info['part_no']))
                    voters = [dict(r) for r in cur.fetchall()]

    cfg = load_tenant_config(tenant)
    community_tags = get_cached_community_tags(repo, tenant, cfg)
    return jsonify({
        'success': True,
        'voters': voters,
        'total': len(voters),
        'community_tags': community_tags
    })


# ===========================================================================
# KARYAKARTA WHATSAPP DISPATCH
# ===========================================================================

@karyakartas_bp.route('/api/karyakartas/<int:k_id>/send_whatsapp', methods=['POST'])
@operator_or_super_admin_required
def api_karyakarta_send_whatsapp(k_id: int):
    """Generates voter assignment list and dispatches it directly to karyakarta via Meta WhatsApp."""
    tenant, repo = _get_tenant()
    tid, slug = repo._resolve_tenant(tenant)
    cfg = load_tenant_config(slug)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT karyakarta_id as id, name, phone, role, part_no as booth_no, access_token
                FROM karyakartas
                WHERE tenant_id = %s AND karyakarta_id = %s
            """, (tid, k_id))
            k_info = cur.fetchone()
            if not k_info:
                return jsonify({'success': False, 'error': 'Karyakarta not found'}), 404

            k_dict = dict(k_info)
            phone = k_dict.get('phone')
            if not phone:
                return jsonify({'success': False, 'error': 'Karyakarta phone number is missing'}), 400

            access_token = k_dict.get('access_token')
            if not access_token:
                access_token = secrets.token_urlsafe(24)
                cur.execute("UPDATE karyakartas SET access_token = %s, is_active = TRUE WHERE karyakarta_id = %s", (access_token, k_id))
                conn.commit()

            cur.execute("""
                SELECT voter_id as id, part_no, sl_no, name_en, name_hi, rel_name_en, rel_name_hi, rel_type,
                       house_no, epic_no, age, gender, contact_phone, voter_category, is_voted, voted_at, is_outside
                FROM voters
                WHERE tenant_id = %s AND assigned_karyakarta_id = %s
                ORDER BY part_no, house_no, sl_no
            """, (tid, k_id))
            voters = [dict(r) for r in cur.fetchall()]

    total = len(voters)
    voted = sum(1 for v in voters if v.get('is_voted'))
    pending = total - voted

    base_url = request.host_url.rstrip('/')
    magic_url = f"{base_url}/k/{access_token}"

    ward_hi = cfg.get('election', {}).get('ward_name_hi', f'वार्ड {slug}')
    lines = [
        f"📋 *मतदाता दायित्व सूची ({ward_hi})*",
        f"👤 *कार्यकर्ता:* {k_dict.get('name') or ''}",
        f"📞 *मोबाइल:* {phone}",
        f"🗳️ *बूथ/क्षेत्र:* भाग {k_dict.get('booth_no') or 'समस्त'} {k_dict.get('role') or ''}",
        f"📊 *कुल ज़िम्मेदारी मतदाता:* {total} (वोट डाले: {voted} | शेष: {pending})",
        "------------------------------------",
        "🔗 *सीधा लॉगिन लिंक (बिना पासवर्ड):*",
        f"{magic_url}",
        "*(इस लिंक को दबाते ही आपकी मतदाता सूची और पर्चियां सीधे खुल जाएंगी)*",
        "------------------------------------"
    ]

    for idx, v in enumerate(voters, 1):
        status_icon = "✅" if v.get('is_voted') else "⏳"
        cat_badge = ""
        if str(v.get('voter_category')) == '100':
            cat_badge = " [🟢 पक्का]"
        elif str(v.get('voter_category')) in ('50_50', '50'):
            cat_badge = " [🟡 50-50]"

        outside_badge = " [✈️ बाहर/प्रवासी]" if v.get('is_outside') else ""
        p_str = f" | 📱 {v['contact_phone']}" if v.get('contact_phone') else ""
        name_str = v.get('name_hi') or v.get('name_en') or ''

        lines.append(f"{idx}. {status_icon} *{name_str}* (भाग {v['part_no']} क्रम #{v['sl_no']}) - म.नं. {v['house_no'] or '-'}{cat_badge}{outside_badge}{p_str}")

    lines.append("------------------------------------")
    ward_disp = cfg.get('election', {}).get('ward_name_hi', 'वार्ड')
    cand_disp = cfg.get('candidate', {}).get('name_hi', '')
    lines.append(f"{ward_disp} चुनाव वॉर-रूम · {cand_disp} चुनाव कार्यालय")
    wa_message = "\n".join(lines)

    # WhatsApp quota gate. One credit for this one message; fails CLOSED.
    try:
        credit = whatsapp_quota.reserve(tid, 1)
    except whatsapp_quota.QuotaError as qe:
        return jsonify({
            'success': False,
            'phone': phone,
            'karyakarta_name': k_dict.get('name'),
            'error': str(qe),
            'quota_blocked': True
        }), 429

    booth_no = k_dict.get('booth_no') or 'समस्त'
    area_role = k_dict.get('role') or ''
    summary_text = f"बूथ #{booth_no} ({area_role}) · कुल दायित्व मतदाता: {total} (बाकी: {pending})"
    sender_sig = f"— {ward_hi} चुनाव वॉर-रूम"

    text_to_dispatch = wa_message if len(wa_message) <= 3500 else None

    try:
        with credit:
            success, data, err = meta_whatsapp.send_karyakarta_magic_link_template(
                phone=phone,
                worker_name=k_dict.get('name') or 'कार्यकर्ता साथी',
                assignment_summary=summary_text,
                magic_url=magic_url,
                sender_label=sender_sig,
                credit=credit
            )
    except Exception as e:
        logger.exception("Failed to dispatch WhatsApp magic link to karyakarta %s: %s", k_id, e)
        return jsonify({
            'success': False,
            'phone': phone,
            'karyakarta_name': k_dict.get('name'),
            'error': f'तकनीकी समस्या के कारण संदेश नहीं भेजा जा सका: {str(e)}'
        }), 500

    return jsonify({
        'success': success,
        'phone': phone,
        'karyakarta_name': k_dict.get('name'),
        'magic_link': magic_url,
        'template_name': meta_whatsapp.DEFAULT_TEMPLATE_NAME,
        'channel': 'Meta Cloud API (Template info_update_short)',
        'text_roster': wa_message,
        'error': err,
        'response': data
    })
