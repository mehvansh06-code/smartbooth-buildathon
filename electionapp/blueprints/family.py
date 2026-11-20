"""
family.py
Family Profiling, Mukhiya / Head of Family & Household Grouping Blueprint:
- /api/family/set_head, /api/voter/toggle_mukhiya — Set or toggle family head (Mukhiya) status
- /api/family/edit_id                            — Update family ID for voter or entire household
- /api/voter/bulk_family_id                      — Bulk assign family_id to multiple voters
- /api/families/bulk_caste                       — Bulk assign caste_tag by voter_ids or family_id
"""

import re
from typing import Any, Dict, List, Optional, Tuple
import psycopg2.extras
from flask import Blueprint, jsonify, request, g, session

from electionapp.audit import audit_action
from electionapp.tenancy import resolve_tenant, mukhiya_prefers_male
from electionapp.translit.bilingual_search import get_bilingual_search_variants
from electionapp.translit.rules import transliterate_name

family_bp = Blueprint('family', __name__)


def _get_tenant():
    """Resolve active tenant slug and repo using authoritative tenancy resolver."""
    return resolve_tenant()


def _parse_location_filter(ward_arg: Any = None, part_arg: Any = None) -> Tuple[Optional[int], Optional[int], Optional[List[int]], Optional[str]]:
    """
    Robustly parses ward_no, part_no, part_nos, and gram_panchayat from location parameters.
    Supports gp:GP_ward:1, compound 'part:9_ward:1' and multi-part 'parts:9,10'.
    """
    from electionapp.config import parse_location_filter_extended
    return parse_location_filter_extended(ward_arg, part_arg)



# ===========================================================================
# FAMILY PROFILING & MUKHIYA MANAGEMENT
# ===========================================================================

@family_bp.route('/api/family/set_head', methods=['POST'])
@family_bp.route('/api/voter/toggle_mukhiya', methods=['POST'])
@audit_action('family.set_head', entity_type='family')
def api_family_set_head():
    """Sets or toggles family head / mukhiya status for a voter."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(silent=True) or request.form or {}
    voter_id = data.get('voter_id') or data.get('id')
    if not voter_id:
        return jsonify({'success': False, 'error': 'voter_id is required'}), 400

    try:
        vid = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter_id'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, legacy_id, part_no, family_id, is_family_head
                FROM voters
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                LIMIT 1
            """, (tid, vid, vid))
            voter = cur.fetchone()
            if not voter:
                return jsonify({'success': False, 'error': 'Voter not found'}), 404

            part_no = voter['part_no']
            family_id = voter['family_id']
            curr_head = bool(voter['is_family_head'])

            if 'is_mukhiya' in data:
                new_head_val = bool(data['is_mukhiya'])
            elif 'is_family_head' in data:
                new_head_val = bool(data['is_family_head'])
            elif data.get('toggle') and curr_head:
                new_head_val = False
            else:
                new_head_val = True

            if family_id and new_head_val:
                cur.execute("""
                    UPDATE voters
                    SET is_family_head = FALSE
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                """, (tid, part_no, family_id))

            cur.execute("""
                UPDATE voters
                SET is_family_head = %s
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
            """, (new_head_val, tid, vid, vid))
        conn.commit()

    return jsonify({
        'success': True,
        'voter_id': vid,
        'is_family_head': 1 if new_head_val else 0,
        'is_mukhiya': 1 if new_head_val else 0,
        'message': 'Family head updated successfully'
    })


@family_bp.route('/api/family/edit_id', methods=['POST'])
@audit_action('family.reassign', entity_type='family')
def api_family_edit_id():
    """Updates family ID for voter or entire household, managing head and counts."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    head_order_sql = "(gender = 'M') DESC, age DESC, sl_no ASC" if mukhiya_prefers_male(tenant) else "age DESC, sl_no ASC"

    data = request.get_json(silent=True) or request.form or {}
    voter_id = data.get('voter_id') or data.get('id')
    new_family_id = data.get('new_family_id')
    apply_household = bool(data.get('apply_household', False))

    if not voter_id:
        return jsonify({'success': False, 'error': 'voter_id is required'}), 400

    try:
        vid = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter_id'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, legacy_id, part_no, house_no, family_id, is_family_head
                FROM voters
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                LIMIT 1
            """, (tid, vid, vid))
            voter = cur.fetchone()
            if not voter:
                return jsonify({'success': False, 'error': 'Voter not found'}), 404

            part_no = voter['part_no']
            house_no = voter['house_no']
            old_family_id = voter['family_id']

            if new_family_id is not None:
                new_family_id = str(new_family_id).strip()
                if new_family_id.lower() in ('generate', 'new', 'auto'):
                    cur.execute("""
                        SELECT MAX(NULLIF(regexp_replace(family_id, '\\D', '', 'g'), '')::bigint) as max_val
                        FROM voters
                        WHERE tenant_id = %s
                    """, (tid,))
                    max_row = cur.fetchone()
                    max_val = max_row['max_val'] if max_row and max_row.get('max_val') else 30000
                    new_family_id = str(int(max_val) + 1)
                elif new_family_id == '' or new_family_id.lower() in ('none', 'null'):
                    new_family_id = None

            if apply_household and house_no:
                clean_hno = str(house_no).lstrip('0') or '0'
                cur.execute("""
                    UPDATE voters
                    SET family_id = %s, family_source_type = 'MANUAL'
                    WHERE tenant_id = %s AND part_no = %s AND (house_no = %s OR ltrim(house_no, '0') = %s)
                """, (new_family_id, tid, part_no, house_no, clean_hno))
            else:
                cur.execute("""
                    UPDATE voters
                    SET family_id = %s, family_source_type = 'MANUAL'
                    WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                """, (new_family_id, tid, vid, vid))

            if new_family_id:
                cur.execute("""
                    SELECT count(*) as cnt FROM voters
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                """, (tid, part_no, new_family_id))
                new_cnt = cur.fetchone()['cnt']

                cur.execute("""
                    UPDATE voters
                    SET family_count = %s
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                """, (new_cnt, tid, part_no, new_family_id))

                cur.execute("""
                    SELECT count(*) as cnt FROM voters
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s AND is_family_head = TRUE
                """, (tid, part_no, new_family_id))
                if cur.fetchone()['cnt'] == 0:
                    cur.execute("""
                        UPDATE voters
                        SET is_family_head = TRUE
                        WHERE tenant_id = %s AND voter_id = (
                            SELECT voter_id FROM voters
                            WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                            ORDER BY {order} LIMIT 1
                        )
                    """.format(order=head_order_sql), (tid, tid, part_no, new_family_id))

            if old_family_id and old_family_id != new_family_id:
                cur.execute("""
                    SELECT count(*) as cnt FROM voters
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                """, (tid, part_no, old_family_id))
                old_cnt = cur.fetchone()['cnt']

                cur.execute("""
                    UPDATE voters
                    SET family_count = %s
                    WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                """, (old_cnt, tid, part_no, old_family_id))

                if old_cnt > 0:
                    cur.execute("""
                        SELECT count(*) as cnt FROM voters
                        WHERE tenant_id = %s AND part_no = %s AND family_id = %s AND is_family_head = TRUE
                    """, (tid, part_no, old_family_id))
                    if cur.fetchone()['cnt'] == 0:
                        cur.execute("""
                            UPDATE voters
                            SET is_family_head = TRUE
                            WHERE tenant_id = %s AND voter_id = (
                                SELECT voter_id FROM voters
                                WHERE tenant_id = %s AND part_no = %s AND family_id = %s
                                ORDER BY {order} LIMIT 1
                            )
                        """.format(order=head_order_sql), (tid, tid, part_no, old_family_id))
        conn.commit()

    return jsonify({
        'success': True,
        'voter_id': vid,
        'family_id': new_family_id,
        'old_family_id': old_family_id,
        'message': 'Family ID updated successfully'
    })


@family_bp.route('/api/family/remove_member', methods=['POST'])
@audit_action('family.remove_member', entity_type='family')
def api_family_remove_member():
    """
    Removes a voter from a family grouping.
    - If family is under an explicit family_id:
        Sets voter's family_id = NULL, family_source_type = 'MANUAL', is_family_head = FALSE.
        Recalculates family_count for remaining members.
        If removed voter was Mukhiya, auto-elects the most senior remaining member as Mukhiya.
    - If family is under a house fallback 'H_{house_no}@{ward}':
        Auto-assigns a new explicit numeric family_id to all OTHER members sharing that house_no,
        while leaving the removed voter with family_id = NULL. This cleanly isolates the removed
        voter from the remaining household group.
    """
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    head_order_sql = "(gender = 'M') DESC, age DESC, sl_no ASC" if mukhiya_prefers_male(tenant) else "age DESC, sl_no ASC"

    data = request.get_json(silent=True) or request.form or {}
    voter_id = data.get('voter_id') or data.get('id')

    if not voter_id:
        return jsonify({'success': False, 'error': 'voter_id is required'}), 400

    try:
        vid = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter_id'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, legacy_id, part_no, ward_no, house_no, family_id, is_family_head
                FROM voters
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                LIMIT 1
            """, (tid, vid, vid))
            voter = cur.fetchone()
            if not voter:
                return jsonify({'success': False, 'error': 'Voter not found'}), 404

            part_no = voter['part_no']
            house_no = voter['house_no']
            old_family_id = voter['family_id']
            was_head = bool(voter['is_family_head'])

            # Case A: Explicit family_id already assigned
            if old_family_id and str(old_family_id).strip() and not str(old_family_id).startswith('H_') and not str(old_family_id).startswith('V_'):
                fid = str(old_family_id).strip()
                cur.execute("""
                    UPDATE voters
                    SET family_id = NULL, family_source_type = 'MANUAL', is_family_head = FALSE, family_count = 1
                    WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                """, (tid, vid, vid))

                cur.execute("""
                    SELECT count(*) as cnt FROM voters
                    WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                """, (tid, fid))
                rem_cnt = cur.fetchone()['cnt']

                cur.execute("""
                    UPDATE voters
                    SET family_count = %s
                    WHERE tenant_id = %s AND family_id = %s
                """, (rem_cnt, tid, fid))

                if rem_cnt > 0 and was_head:
                    cur.execute(f"""
                        UPDATE voters
                        SET is_family_head = TRUE
                        WHERE tenant_id = %s AND voter_id = (
                            SELECT voter_id FROM voters
                            WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                            ORDER BY {head_order_sql}
                            LIMIT 1
                        )
                    """, (tid, tid, fid))

                conn.commit()
                return jsonify({
                    'success': True,
                    'voter_id': vid,
                    'family_id': None,
                    'old_family_id': fid,
                    'remaining_members': rem_cnt,
                    'message': 'मतदाता को परिवार से अलग कर दिया गया है'
                })

            # Case B: Household fallback 'H_...' or unassigned
            clean_hno = str(house_no).lstrip('0') or '0' if house_no else ''
            other_voter_ids = []
            if house_no:
                cur.execute("""
                    SELECT voter_id FROM voters
                    WHERE tenant_id = %s AND part_no = %s
                      AND (house_no = %s OR ltrim(house_no, '0') = %s)
                      AND (voter_id != %s AND legacy_id != %s)
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                """, (tid, part_no, house_no, clean_hno, vid, vid))
                other_voter_ids = [r['voter_id'] for r in cur.fetchall()]

            if other_voter_ids:
                # Generate new explicit family_id for all remaining household members so they stay together
                cur.execute("""
                    SELECT MAX(NULLIF(regexp_replace(family_id, '\\D', '', 'g'), '')::bigint) as max_val
                    FROM voters
                    WHERE tenant_id = %s
                """, (tid,))
                max_row = cur.fetchone()
                max_val = max_row['max_val'] if max_row and max_row.get('max_val') else 30000
                new_fid = str(int(max_val) + 1)
                rem_cnt = len(other_voter_ids)

                cur.execute("""
                    UPDATE voters
                    SET family_id = %s, family_source_type = 'MANUAL', family_count = %s
                    WHERE tenant_id = %s AND voter_id = ANY(%s)
                """, (new_fid, rem_cnt, tid, other_voter_ids))

                # Ensure remaining household members have a Mukhiya
                cur.execute(f"""
                    UPDATE voters
                    SET is_family_head = TRUE
                    WHERE tenant_id = %s AND voter_id = (
                        SELECT voter_id FROM voters
                        WHERE tenant_id = %s AND voter_id = ANY(%s)
                        ORDER BY {head_order_sql}
                        LIMIT 1
                    )
                """, (tid, tid, other_voter_ids))
            else:
                rem_cnt = 0

            # Detach target voter
            cur.execute("""
                UPDATE voters
                SET family_id = NULL, family_source_type = 'MANUAL', is_family_head = FALSE, family_count = 1
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
            """, (tid, vid, vid))
            conn.commit()

    return jsonify({
        'success': True,
        'voter_id': vid,
        'family_id': None,
        'remaining_members': rem_cnt,
        'message': 'मतदाता को परिवार से अलग कर दिया गया है'
    })


@family_bp.route('/api/family/add_member', methods=['POST'])
@audit_action('family.add_member', entity_type='family')
def api_family_add_member():
    """
    Adds a voter into a family.
    - If target_family_id is an explicit family_id:
        Assigns family_id to voter, recalculates counts for old and new families,
        ensuring Mukhiya integrity.
    - If target_family_id is a house fallback 'H_...':
        Auto-assigns a new explicit numeric family_id to existing household members
        plus the added voter.
    """
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    head_order_sql = "(gender = 'M') DESC, age DESC, sl_no ASC" if mukhiya_prefers_male(tenant) else "age DESC, sl_no ASC"

    data = request.get_json(silent=True) or request.form or {}
    voter_id = data.get('voter_id') or data.get('id')
    target_fid = str(data.get('target_family_id') or data.get('family_id') or '').strip()

    if not voter_id:
        return jsonify({'success': False, 'error': 'voter_id is required'}), 400
    if not target_fid:
        return jsonify({'success': False, 'error': 'target_family_id is required'}), 400

    try:
        vid = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter_id'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, legacy_id, part_no, ward_no, house_no, family_id, is_family_head
                FROM voters
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                LIMIT 1
            """, (tid, vid, vid))
            voter = cur.fetchone()
            if not voter:
                return jsonify({'success': False, 'error': 'Voter not found'}), 404

            old_family_id = voter['family_id']
            old_was_head = bool(voter['is_family_head'])

            # Determine target family ID
            is_house_fallback = target_fid.startswith('H_') or target_fid.startswith('V_')
            if is_house_fallback:
                # Generate new numeric family_id
                cur.execute("""
                    SELECT MAX(NULLIF(regexp_replace(family_id, '\\D', '', 'g'), '')::bigint) as max_val
                    FROM voters
                    WHERE tenant_id = %s
                """, (tid,))
                max_row = cur.fetchone()
                max_val = max_row['max_val'] if max_row and max_row.get('max_val') else 30000
                assigned_fid = str(int(max_val) + 1)

                # Find all current members of this house fallback
                raw_token = target_fid.split('@')[0]
                target_house_no = raw_token[2:] if raw_token.startswith('H_') else ''
                clean_target_hno = str(target_house_no).lstrip('0') or '0'
                target_ward_part = target_fid.split('@')[1] if '@' in target_fid else ''

                if target_house_no:
                    cur.execute("""
                        UPDATE voters
                        SET family_id = %s, family_source_type = 'MANUAL'
                        WHERE tenant_id = %s
                          AND (house_no = %s OR ltrim(house_no, '0') = %s)
                          AND (ward_no = %s OR part_no = %s OR %s = '')
                    """, (assigned_fid, tid, target_house_no, clean_target_hno, target_ward_part, target_ward_part, target_ward_part))
            else:
                assigned_fid = target_fid

            # Add voter into assigned family
            cur.execute("""
                UPDATE voters
                SET family_id = %s, family_source_type = 'MANUAL', is_family_head = FALSE
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
            """, (assigned_fid, tid, vid, vid))

            # Recalculate target family count
            cur.execute("""
                SELECT count(*) as cnt FROM voters
                WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
            """, (tid, assigned_fid))
            new_fam_cnt = cur.fetchone()['cnt']

            cur.execute("""
                UPDATE voters
                SET family_count = %s
                WHERE tenant_id = %s AND family_id = %s
            """, (new_fam_cnt, tid, assigned_fid))

            # Ensure Mukhiya exists in target family
            cur.execute("""
                SELECT count(*) as cnt FROM voters
                WHERE tenant_id = %s AND family_id = %s AND is_family_head = TRUE AND LOWER(COALESCE(status, '')) <> 'deleted'
            """, (tid, assigned_fid))
            if cur.fetchone()['cnt'] == 0:
                cur.execute(f"""
                    UPDATE voters
                    SET is_family_head = TRUE
                    WHERE tenant_id = %s AND voter_id = (
                        SELECT voter_id FROM voters
                        WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                        ORDER BY {head_order_sql}
                        LIMIT 1
                    )
                """, (tid, tid, assigned_fid))

            # Clean up old family if voter had an explicit family previously
            if old_family_id and str(old_family_id).strip() != assigned_fid and not str(old_family_id).startswith('H_') and not str(old_family_id).startswith('V_'):
                cur.execute("""
                    SELECT count(*) as cnt FROM voters
                    WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                """, (tid, old_family_id))
                old_rem_cnt = cur.fetchone()['cnt']

                cur.execute("""
                    UPDATE voters
                    SET family_count = %s
                    WHERE tenant_id = %s AND family_id = %s
                """, (old_rem_cnt, tid, old_family_id))

                if old_rem_cnt > 0 and old_was_head:
                    cur.execute(f"""
                        UPDATE voters
                        SET is_family_head = TRUE
                        WHERE tenant_id = %s AND voter_id = (
                            SELECT voter_id FROM voters
                            WHERE tenant_id = %s AND family_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                            ORDER BY {head_order_sql}
                            LIMIT 1
                        )
                    """, (tid, tid, old_family_id))

            conn.commit()

    return jsonify({
        'success': True,
        'voter_id': vid,
        'family_id': assigned_fid,
        'total_members': new_fam_cnt,
        'message': 'मतदाता को परिवार में जोड़ दिया गया है'
    })


@family_bp.route('/api/family/search_candidates', methods=['GET'])
def api_family_search_candidates():
    """
    Search candidate voters to add into a family.
    Supports ?q=... (name in English/Hindi, EPIC, relative, phone, serial),
    ?ward_no=..., ?part_no=..., ?exclude_ids=...
    """
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    q = (request.args.get('q') or request.args.get('search') or '').strip()
    if not q or len(q) < 2:
        return jsonify({'success': True, 'candidates': []})

    raw_ward = request.args.get('ward_no') or request.args.get('ward')
    raw_part = request.args.get('part_no') or request.args.get('part')
    ward_no, part_no, part_nos, gp, ward_nos = _parse_location_filter(raw_ward, raw_part)
    phone_status = (request.args.get('phone_status') or request.args.get('phone') or '').strip().lower()
    caste_status = (request.args.get('caste_status') or '').strip().lower()
    sentiment_status = (request.args.get('sentiment_status') or request.args.get('sentiment') or '').strip().lower()
    size_filter = (request.args.get('size_filter') or request.args.get('size') or '').strip().lower()
    exclude_str = request.args.get('exclude_ids') or ''
    exclude_ids = [int(x) for x in exclude_str.split(',') if x.strip().isdigit()]

    variants_info = get_bilingual_search_variants(q)
    params = {
        'tenant_id': tid,
        'raw_q': f"%{q}%",
        'hi_patterns': variants_info.get('hi_patterns') or [f"%{q}%"],
        'en_patterns': variants_info.get('en_patterns') or [f"%{q}%"],
        'all_patterns': variants_info.get('all_patterns') or [f"%{q}%"],
        'limit': 20
    }

    where_clauses = [
        "v.tenant_id = %(tenant_id)s",
        "LOWER(COALESCE(v.status, '')) <> 'deleted'"
    ]

    if exclude_ids:
        where_clauses.append("NOT (v.voter_id = ANY(%(exclude_ids)s))")
        params['exclude_ids'] = exclude_ids

    # Location scoping
    if ward_no and part_no:
        where_clauses.append("v.ward_no = %(ward_no)s AND v.part_no = %(part_no)s")
        params['ward_no'] = ward_no
        params['part_no'] = part_no
    elif ward_no and str(ward_no).isdigit() and int(ward_no) > 0:
        where_clauses.append("(v.ward_no = %(ward_no)s OR (COALESCE(v.ward_no, 0) = 0 AND v.part_no = %(ward_no)s))")
        params['ward_no'] = int(ward_no)
    elif part_no and str(part_no).isdigit() and int(part_no) > 0:
        where_clauses.append("(v.part_no = %(part_no)s OR (v.part_no IS NULL AND v.ward_no = %(part_no)s))")
        params['part_no'] = int(part_no)

    # Search pattern clause
    if q.isdigit():
        where_clauses.append("""(
            v.sl_no = %(int_q)s
            OR v.contact_phone ILIKE %(raw_q)s
            OR v.epic_no ILIKE %(raw_q)s
            OR v.house_no = %(str_q)s
        )""")
        params['int_q'] = int(q)
        params['str_q'] = q
    else:
        where_clauses.append("""(
            v.name_hi ILIKE ANY(%(hi_patterns)s)
            OR v.name_en ILIKE ANY(%(en_patterns)s)
            OR v.rel_name_hi ILIKE ANY(%(hi_patterns)s)
            OR v.rel_name_en ILIKE ANY(%(en_patterns)s)
            OR v.epic_no ILIKE %(raw_q)s
            OR v.contact_phone ILIKE %(raw_q)s
            OR v.caste_tag ILIKE ANY(%(all_patterns)s)
        )""")

    where_sql = " AND ".join(where_clauses)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    v.voter_id,
                    v.legacy_id,
                    v.ward_no,
                    v.part_no,
                    v.sl_no,
                    v.name_hi,
                    v.name_en,
                    v.rel_type,
                    v.rel_name_hi,
                    v.rel_name_en,
                    v.house_no,
                    v.age,
                    v.gender,
                    v.epic_no,
                    v.contact_phone,
                    v.caste_tag,
                    v.voter_category,
                    v.family_id,
                    v.is_family_head
                FROM voters v
                WHERE {where_sql}
                ORDER BY
                    CASE WHEN v.name_hi ILIKE ANY(%(hi_patterns)s) OR v.name_en ILIKE ANY(%(en_patterns)s) THEN 0 ELSE 1 END,
                    v.sl_no ASC
                LIMIT %(limit)s;
            """, params)
            candidates = [dict(r) for r in cur.fetchall()]

    return jsonify({
        'success': True,
        'candidates': candidates
    })


@family_bp.route('/api/voter/bulk_family_id', methods=['POST'])
@audit_action('family.bulk_reassign', entity_type='family')
def api_voter_bulk_family_id():
    """Bulk assigns family_id to multiple voters."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(silent=True) or request.form or {}
    voter_ids = data.get('voter_ids') or []
    family_id = data.get('family_id')

    if not voter_ids:
        return jsonify({'success': False, 'error': 'No voters selected'}), 400

    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return jsonify({'success': False, 'error': 'Invalid voter IDs'}), 400

    if family_id is not None:
        family_id = str(family_id).strip()
        if family_id == '' or family_id.lower() in ('none', 'null'):
            family_id = None

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET family_id = %s, family_source_type = 'MANUAL'
                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
            """, (family_id, tid, clean_ids, clean_ids))
            count = cur.rowcount
        conn.commit()

    return jsonify({'success': True, 'count': count, 'family_id': family_id})


@family_bp.route('/api/families/bulk_caste', methods=['POST'])
@audit_action('family.bulk_caste_tag', entity_type='family')
def api_families_bulk_caste():
    """Bulk assigns caste_tag by voter_ids or family_id."""
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(silent=True) or request.form or {}
    voter_ids = data.get('voter_ids', [])
    family_id = data.get('family_id')
    caste_tag = str(data.get('caste_tag') or '').strip()

    if not caste_tag:
        return jsonify({'success': False, 'error': 'caste_tag required'}), 400

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            if voter_ids and isinstance(voter_ids, list):
                clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
                if clean_ids:
                    cur.execute("""
                        UPDATE voters
                        SET caste_tag = %s
                        WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                    """, (caste_tag, tid, clean_ids, clean_ids))
                    updated_count = cur.rowcount
                else:
                    return jsonify({'success': False, 'error': 'No valid voter IDs'}), 400
            elif family_id:
                cur.execute("""
                    UPDATE voters
                    SET caste_tag = %s
                    WHERE tenant_id = %s AND family_id = %s
                """, (caste_tag, tid, str(family_id)))
                updated_count = cur.rowcount
            else:
                return jsonify({'success': False, 'error': 'voter_ids or family_id required'}), 400
        conn.commit()

    return jsonify({'success': True, 'count': updated_count, 'caste_tag': caste_tag})


@family_bp.route('/api/families/summary', methods=['GET'])
def api_families_summary():
    """
    Returns aggregated household/family clusters grouped by family_id or household anchor,
    with dynamically determined Mukhiya (family head).
    Supports ?ward_no=..., ?part_no=..., ?q=... (Bilingual English/Hindi Mukhiya, voter, relative,
    phone, epic, or caste search), ?caste=..., ?limit=...
    """
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    if not tid or not hasattr(repo, 'get_connection'):
        return jsonify({'success': False, 'error': 'Invalid tenant or repo'}), 400

    q = (request.args.get('q') or request.args.get('search') or '').strip()
    raw_ward = request.args.get('ward_no') or request.args.get('ward')
    raw_part = request.args.get('part_no') or request.args.get('part') or request.args.get('booth')
    ward_no, part_no, part_nos, gp, ward_nos = _parse_location_filter(raw_ward, raw_part)
    phone_status = (request.args.get('phone_status') or request.args.get('phone') or '').strip().lower()
    caste_status = (request.args.get('caste_status') or '').strip().lower()
    sentiment_status = (request.args.get('sentiment_status') or request.args.get('sentiment') or '').strip().lower()
    size_filter = (request.args.get('size_filter') or request.args.get('size') or '').strip().lower()
    if not gp:
        gp = request.args.get('gp') or request.args.get('gram_panchayat')
    caste = (request.args.get('caste') or (request.args.get('caste_status') if request.args.get('caste_status') not in ('untagged', 'no_caste', 'unspecified', '') else '') or '').strip()
    raw_limit = request.args.get('limit')
    if raw_limit is not None:
        raw_limit_str = str(raw_limit).strip().lower()
        if raw_limit_str in ('all', '0', '-1', 'none', 'max'):
            limit = 5000
        else:
            try:
                parsed_val = int(raw_limit_str)
                if parsed_val in (0, -1) or parsed_val < 0:
                    limit = 5000
                elif parsed_val > 5000:
                    limit = 5000
                else:
                    limit = parsed_val
            except (ValueError, TypeError):
                limit = 500
    else:
        limit = 500

    params = {'tenant_id': tid, 'limit': limit}
    extra_filter_clauses = []

    if ward_no and part_no:
        extra_filter_clauses.append("v.ward_no = %(ward_no)s AND v.part_no = %(part_no)s")
        params['ward_no'] = ward_no
        params['part_no'] = part_no
    elif ward_no and str(ward_no).isdigit() and int(ward_no) > 0:
        w_val = int(ward_no)
        extra_filter_clauses.append("(v.ward_no = %(ward_no)s OR (COALESCE(v.ward_no, 0) = 0 AND v.part_no = %(ward_no)s))")
        params['ward_no'] = w_val
    elif part_no and str(part_no).isdigit() and int(part_no) > 0:
        p_val = int(part_no)
        extra_filter_clauses.append("(v.part_no = %(part_no)s OR (v.part_no IS NULL AND v.ward_no = %(part_no)s))")
        params['part_no'] = p_val
    elif ward_nos:
        extra_filter_clauses.append("v.ward_no = ANY(%(ward_nos)s)")
        params['ward_nos'] = ward_nos
    elif part_nos:
        extra_filter_clauses.append("v.part_no = ANY(%(part_nos)s)")
        params['part_nos'] = part_nos

    if gp:
        extra_filter_clauses.append("v.gram_panchayat = %(gp)s")
        params['gp'] = gp

    # Scoped user access check
    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = curr_user.get('role') or session.get('role', '')
    is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or curr_user.get('tenant_slug') == '*')

    if not is_super:
        assigned_booths = list(curr_user.get('assigned_booths') or session.get('assigned_booths') or [])
        assigned_booth = curr_user.get('assigned_booth') if curr_user.get('assigned_booth') is not None else session.get('assigned_booth')
        if assigned_booth is not None and assigned_booth not in assigned_booths:
            assigned_booths.append(assigned_booth)
        if assigned_booths and not part_no:
            extra_filter_clauses.append("v.part_no = ANY(%(assigned_booths)s)")
            params['assigned_booths'] = assigned_booths

        assigned_wards = list(curr_user.get('assigned_wards') or session.get('assigned_wards') or [])
        if assigned_wards and not ward_no:
            extra_filter_clauses.append("v.ward_no = ANY(%(assigned_wards)s)")
            params['assigned_wards'] = assigned_wards

    if caste:
        extra_filter_clauses.append("LOWER(TRIM(v.caste_tag)) = LOWER(%(caste)s)")
        params['caste'] = caste

    extra_sql = (" AND " + " AND ".join(extra_filter_clauses)) if extra_filter_clauses else ""

    having_clauses = []
    if phone_status in ('no_phone', 'missing', 'without_phone'):
        having_clauses.append("COUNT(CASE WHEN fm.contact_phone IS NOT NULL AND length(trim(fm.contact_phone)) >= 10 THEN 1 END) = 0")
    elif phone_status in ('has_phone', 'with_phone', 'available'):
        having_clauses.append("COUNT(CASE WHEN fm.contact_phone IS NOT NULL AND length(trim(fm.contact_phone)) >= 10 THEN 1 END) > 0")

    if caste_status in ('untagged', 'no_caste', 'unspecified'):
        having_clauses.append("(MAX(fm.caste_tag) IS NULL OR trim(MAX(fm.caste_tag)) = '')")

    if sentiment_status in ('pending', 'no_sentiment', 'unspecified'):
        having_clauses.append("(SUM(CASE WHEN fm.voter_category IS NOT NULL THEN 1 ELSE 0 END) = 0)")
    elif sentiment_status in ('favored', 'supporter', '100'):
        having_clauses.append("(SUM(CASE WHEN fm.voter_category = 100 THEN 1 ELSE 0 END) > 0)")
    elif sentiment_status in ('opposed', 'opposition', '-1'):
        having_clauses.append("(SUM(CASE WHEN fm.voter_category = -1 THEN 1 ELSE 0 END) > 0)")
    elif sentiment_status in ('swing', 'doubtful', '50'):
        having_clauses.append("(SUM(CASE WHEN fm.voter_category = 50 THEN 1 ELSE 0 END) > 0)")

    if size_filter == 'large':
        having_clauses.append("COUNT(*) >= 5")
    elif size_filter == 'medium':
        having_clauses.append("COUNT(*) BETWEEN 3 AND 4")
    elif size_filter == 'small':
        having_clauses.append("COUNT(*) <= 2")

    having_sql_fm = (" HAVING " + " AND ".join(having_clauses)) if having_clauses else ""
    having_sql_fg = having_sql_fm.replace("fm.", "fg.")

    variants_info = get_bilingual_search_variants(q) if q else {}
    primary_hi = variants_info.get('primary_hi', '')
    male_tier_sql = "CASE WHEN v.gender = 'M' THEN 0 ELSE 1 END," if mukhiya_prefers_male(tenant) else ""

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                params['hi_patterns'] = variants_info.get('hi_patterns') or [f"%{q}%"]
                params['en_patterns'] = variants_info.get('en_patterns') or [f"%{q}%"]
                params['all_patterns'] = variants_info.get('all_patterns') or [f"%{q}%"]
                params['raw_q'] = f"%{q}%"

                sql = f"""
                    WITH matched_voters AS (
                        SELECT 
                            CASE 
                                WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                    THEN trim(v.family_id)
                                WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                    THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                ELSE 'V_' || v.voter_id 
                            END AS fam_key,
                            CASE 
                                WHEN v.family_head_name ILIKE ANY(%(hi_patterns)s) OR v.family_head_name ILIKE ANY(%(en_patterns)s) THEN 0
                                WHEN v.name_hi ILIKE ANY(%(hi_patterns)s) OR v.name_en ILIKE ANY(%(en_patterns)s) THEN 1
                                WHEN v.rel_name_hi ILIKE ANY(%(hi_patterns)s) OR v.rel_name_en ILIKE ANY(%(en_patterns)s) THEN 2
                                ELSE 3
                            END AS match_rank
                        FROM voters v
                        WHERE v.tenant_id = %(tenant_id)s
                          AND LOWER(COALESCE(v.status, '')) <> 'deleted'
                          {extra_sql}
                          AND (
                              v.family_head_name ILIKE ANY(%(hi_patterns)s)
                              OR v.family_head_name ILIKE ANY(%(en_patterns)s)
                              OR v.name_hi ILIKE ANY(%(hi_patterns)s)
                              OR v.name_en ILIKE ANY(%(en_patterns)s)
                              OR v.rel_name_hi ILIKE ANY(%(hi_patterns)s)
                              OR v.rel_name_en ILIKE ANY(%(en_patterns)s)
                              OR v.contact_phone ILIKE %(raw_q)s
                              OR v.epic_no ILIKE %(raw_q)s
                              OR v.caste_tag ILIKE ANY(%(all_patterns)s)
                              OR v.search_doc ILIKE ANY(%(all_patterns)s)
                          )
                    ),
                    matched_families AS (
                        SELECT fam_key, MIN(match_rank) AS min_rank
                        FROM matched_voters
                        GROUP BY fam_key
                        ORDER BY MIN(match_rank) ASC
                        LIMIT 2000
                    ),
                    fam_members AS (
                        SELECT 
                            v.voter_id,
                            v.name_hi,
                            v.name_en,
                            v.family_head_name,
                            v.is_family_head,
                            v.age,
                            v.sl_no,
                            v.contact_phone,
                            v.caste_tag,
                            v.voter_category,
                            v.is_voted,
                            v.assigned_karyakarta_name,
                            v.ward_no,
                            v.part_no,
                            v.family_id,
                            CASE 
                                WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                    THEN trim(v.family_id)
                                WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                    THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                ELSE 'V_' || v.voter_id 
                            END AS fam_key,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE 
                                    WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                        THEN trim(v.family_id)
                                    WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                        THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                    ELSE 'V_' || v.voter_id 
                                END
                                ORDER BY
                                    CASE WHEN v.family_head_name IS NOT NULL AND trim(v.family_head_name) != '' THEN 0 ELSE 1 END,
                                    CASE WHEN v.is_family_head = TRUE THEN 0 ELSE 1 END,
                                    {male_tier_sql}
                                    v.age DESC NULLS LAST,
                                    v.sl_no ASC
                            ) as rn,
                            mf.min_rank
                        FROM voters v
                        JOIN matched_families mf ON (
                            CASE 
                                WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                    THEN trim(v.family_id)
                                WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                    THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                ELSE 'V_' || v.voter_id 
                            END
                        ) = mf.fam_key
                        WHERE v.tenant_id = %(tenant_id)s
                          AND LOWER(COALESCE(v.status, '')) <> 'deleted'
                    )
                    SELECT 
                        fm.fam_key AS family_id,
                        COALESCE(NULLIF(head.ward_no, 0), head.part_no) AS ward_no,
                        head.part_no,
                        COALESCE(NULLIF(trim(head.family_head_name), ''), NULLIF(trim(head.name_hi), ''), head.name_en, 'अज्ञात मुखिया') AS mukhiya_name,
                        MAX(fm.caste_tag) AS caste_tag,
                        COUNT(*) AS total_members,
                        MAX(fm.contact_phone) AS primary_phone,
                        SUM(CASE WHEN fm.voter_category = 100 THEN 1 ELSE 0 END) AS favored_count,
                        SUM(CASE WHEN fm.voter_category = -1 THEN 1 ELSE 0 END) AS opposed_count,
                        SUM(CASE WHEN fm.voter_category = 50 THEN 1 ELSE 0 END) AS swing_count,
                        SUM(CASE WHEN fm.is_voted = TRUE THEN 1 ELSE 0 END) AS voted_count,
                        MAX(fm.assigned_karyakarta_name) AS karyakarta_name,
                        ARRAY_AGG(fm.voter_id ORDER BY fm.rn ASC) AS member_ids,
                        ARRAY_AGG(DISTINCT fm.contact_phone) FILTER (WHERE fm.contact_phone IS NOT NULL AND length(trim(fm.contact_phone)) >= 10) AS all_phones,
                        ARRAY_AGG(DISTINCT COALESCE(NULLIF(fm.ward_no, 0), fm.part_no)) FILTER (WHERE COALESCE(NULLIF(fm.ward_no, 0), fm.part_no) IS NOT NULL) AS all_wards
                    FROM fam_members fm
                    JOIN fam_members head ON fm.fam_key = head.fam_key AND head.rn = 1
                    GROUP BY fm.fam_key, head.ward_no, head.part_no, head.family_head_name, head.name_hi, head.name_en, fm.min_rank
                    {having_sql_fm}
                    ORDER BY fm.min_rank ASC, total_members DESC, mukhiya_name ASC
                    LIMIT %(limit)s;
                """
            else:
                where_clauses = ["v.tenant_id = %(tenant_id)s", "LOWER(COALESCE(v.status, '')) <> 'deleted'"]
                if extra_filter_clauses:
                    where_clauses.extend(extra_filter_clauses)
                where_sql = " AND ".join(where_clauses)

                sql = f"""
                    WITH fam_grouped AS (
                        SELECT 
                            v.voter_id,
                            v.name_hi,
                            v.name_en,
                            v.family_head_name,
                            v.is_family_head,
                            v.age,
                            v.sl_no,
                            v.contact_phone,
                            v.caste_tag,
                            v.voter_category,
                            v.is_voted,
                            v.assigned_karyakarta_name,
                            v.ward_no,
                            v.part_no,
                            CASE 
                                WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                    THEN trim(v.family_id)
                                WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                    THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                ELSE 'V_' || v.voter_id 
                            END AS fam_key,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE 
                                    WHEN v.family_id IS NOT NULL AND trim(v.family_id) != '' 
                                        THEN trim(v.family_id)
                                    WHEN v.house_no IS NOT NULL AND trim(v.house_no) NOT IN ('', '0', 'none', 'null') 
                                        THEN 'H_' || trim(v.house_no) || '@' || COALESCE(NULLIF(v.ward_no, 0), v.part_no, 0)
                                    ELSE 'V_' || v.voter_id 
                                END
                                ORDER BY
                                    CASE WHEN v.family_head_name IS NOT NULL AND trim(v.family_head_name) != '' THEN 0 ELSE 1 END,
                                    CASE WHEN v.is_family_head = TRUE THEN 0 ELSE 1 END,
                                    {male_tier_sql}
                                    v.age DESC NULLS LAST,
                                    v.sl_no ASC
                            ) as rn
                        FROM voters v
                        WHERE {where_sql}
                    )
                    SELECT 
                        fg.fam_key AS family_id,
                        COALESCE(NULLIF(head.ward_no, 0), head.part_no) AS ward_no,
                        head.part_no,
                        COALESCE(NULLIF(trim(head.family_head_name), ''), NULLIF(trim(head.name_hi), ''), head.name_en, 'अज्ञात मुखिया') AS mukhiya_name,
                        MAX(fg.caste_tag) AS caste_tag,
                        COUNT(*) AS total_members,
                        MAX(fg.contact_phone) AS primary_phone,
                        SUM(CASE WHEN fg.voter_category = 100 THEN 1 ELSE 0 END) AS favored_count,
                        SUM(CASE WHEN fg.voter_category = -1 THEN 1 ELSE 0 END) AS opposed_count,
                        SUM(CASE WHEN fg.voter_category = 50 THEN 1 ELSE 0 END) AS swing_count,
                        SUM(CASE WHEN fg.is_voted = TRUE THEN 1 ELSE 0 END) AS voted_count,
                        MAX(fg.assigned_karyakarta_name) AS karyakarta_name,
                        ARRAY_AGG(fg.voter_id ORDER BY fg.rn ASC) AS member_ids,
                        ARRAY_AGG(DISTINCT fg.contact_phone) FILTER (WHERE fg.contact_phone IS NOT NULL AND length(trim(fg.contact_phone)) >= 10) AS all_phones,
                        ARRAY_AGG(DISTINCT COALESCE(NULLIF(fg.ward_no, 0), fg.part_no)) FILTER (WHERE COALESCE(NULLIF(fg.ward_no, 0), fg.part_no) IS NOT NULL) AS all_wards
                    FROM fam_grouped fg
                    JOIN fam_grouped head ON fg.fam_key = head.fam_key AND head.rn = 1
                    GROUP BY fg.fam_key, head.ward_no, head.part_no, head.family_head_name, head.name_hi, head.name_en
                    {having_sql_fg}
                    ORDER BY total_members DESC, mukhiya_name ASC
                    LIMIT %(limit)s;
                """

            cur.execute(sql, params)
            families = [dict(r) for r in cur.fetchall()]

            for f in families:
                m_name = f.get('mukhiya_name') or ''
                f['mukhiya_name_en'] = transliterate_name(m_name) if m_name else ''

                raw_phones = f.get('all_phones')
                if raw_phones is None:
                    f['all_phones'] = []
                else:
                    f['all_phones'] = [str(p).strip() for p in raw_phones if p and str(p).strip()]

                raw_member_ids = f.get('member_ids')
                if raw_member_ids is None:
                    f['member_ids'] = []
                else:
                    f['member_ids'] = list(raw_member_ids)

                raw_wards = f.get('all_wards')
                if raw_wards is None:
                    f['all_wards'] = [f['ward_no']] if f.get('ward_no') else []
                else:
                    sorted_wards = sorted([int(w) for w in raw_wards if w is not None and str(w).isdigit()])
                    f['all_wards'] = sorted_wards if sorted_wards else ([f['ward_no']] if f.get('ward_no') else [])

            total_families = len(families)
            total_members = sum(f['total_members'] for f in families)
            avg_members = round(total_members / total_families, 1) if total_families > 0 else 0
            with_phone_count = sum(1 for f in families if f['primary_phone'])
            favored_total = sum(f['favored_count'] for f in families)

    return jsonify({
        'success': True,
        'families': families,
        'query': q,
        'transliterated_query': primary_hi if (primary_hi and primary_hi != q) else '',
        'stats': {
            'total_families': total_families,
            'total_members': total_members,
            'avg_members': avg_members,
            'with_phone_count': with_phone_count,
            'favored_total': favored_total
        }
    })


@family_bp.route('/api/family/members_by_head', methods=['GET'])
@family_bp.route('/api/family/members', methods=['GET'])
def api_family_members_by_head():
    """
    Returns full list of voters belonging to a specific family or Mukhiya.
    Supports ?family_id=..., ?mukhiya_name=..., ?ward_no=..., ?part_no=...
    When family_id is supplied and not a fallback house ('H_...'), returns all members
    across wards.
    """
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    family_id = (request.args.get('family_id') or request.args.get('id') or '').strip()
    mukhiya = (request.args.get('mukhiya_name') or request.args.get('mukhiya') or request.args.get('name') or '').strip()
    raw_ward = request.args.get('ward_no') or request.args.get('ward')
    raw_part = request.args.get('part_no') or request.args.get('part')
    ward_no, part_no, part_nos, gp, ward_nos = _parse_location_filter(raw_ward, raw_part)
    phone_status = (request.args.get('phone_status') or request.args.get('phone') or '').strip().lower()
    caste_status = (request.args.get('caste_status') or '').strip().lower()
    sentiment_status = (request.args.get('sentiment_status') or request.args.get('sentiment') or '').strip().lower()
    size_filter = (request.args.get('size_filter') or request.args.get('size') or '').strip().lower()
    if not gp:
        gp = request.args.get('gp') or request.args.get('gram_panchayat')

    if not family_id and not mukhiya:
        return jsonify({'success': False, 'error': 'family_id or mukhiya_name is required'}), 400

    params = {'tenant_id': tid}
    where_clauses = ["tenant_id = %(tenant_id)s", "LOWER(COALESCE(status, '')) <> 'deleted'"]

    raw_fid = family_id.split('@')[0] if family_id else ''
    is_house_fallback = raw_fid.startswith('H_')
    has_explicit_family_id = bool(raw_fid and not is_house_fallback and not raw_fid.startswith('V_'))

    # Only apply ward/part filter if looking up by house fallback or mukhiya name.
    # When explicit family_id is supplied, do NOT filter by ward_no to return all cross-ward members.
    if not has_explicit_family_id:
        if ward_no and part_no:
            where_clauses.append("ward_no = %(ward_no)s AND part_no = %(part_no)s")
            params['ward_no'] = int(ward_no)
            params['part_no'] = int(part_no)
        elif ward_no and str(ward_no).isdigit() and int(ward_no) > 0:
            where_clauses.append("(ward_no = %(ward_no)s OR (COALESCE(ward_no, 0) = 0 AND part_no = %(ward_no)s))")
            params['ward_no'] = int(ward_no)
        elif part_no and str(part_no).isdigit() and int(part_no) > 0:
            where_clauses.append("(part_no = %(part_no)s OR (part_no IS NULL AND ward_no = %(part_no)s))")
            params['part_no'] = int(part_no)
        elif part_nos:
            where_clauses.append("part_no = ANY(%(part_nos)s)")
            params['part_nos'] = part_nos
        if gp:
            where_clauses.append("gram_panchayat = %(gp)s")
            params['gp'] = gp

    if family_id:
        if raw_fid.startswith('H_'):
            house_no = raw_fid[2:]
            where_clauses.append("(house_no = %(house_no)s OR LTRIM(house_no, '0') = %(clean_house_no)s)")
            params['house_no'] = house_no
            params['clean_house_no'] = house_no.lstrip('0') or '0'
        elif raw_fid.startswith('V_') and raw_fid[2:].isdigit():
            where_clauses.append("voter_id = %(voter_id)s")
            params['voter_id'] = int(raw_fid[2:])
        else:
            where_clauses.append("(family_id = %(family_id)s)")
            params['family_id'] = raw_fid
    else:
        # Lookup by Mukhiya name (Hindi or transliterated)
        variants_info = get_bilingual_search_variants(mukhiya)
        hi_variants = variants_info.get('hi_variants') or []
        if mukhiya not in hi_variants:
            hi_variants.insert(0, mukhiya)
        params['mukhiya_raw'] = mukhiya
        params['hi_variants'] = hi_variants
        where_clauses.append("""
            (
                family_head_name = %(mukhiya_raw)s 
                OR family_head_name = ANY(%(hi_variants)s)
                OR name_hi = %(mukhiya_raw)s
                OR name_hi = ANY(%(hi_variants)s)
                OR name_en ILIKE %(mukhiya_raw)s
            )
        """)

    where_sql = " AND ".join(where_clauses)
    member_order_sql = "is_family_head DESC, (gender = 'M') DESC, age DESC, sl_no ASC" if mukhiya_prefers_male(tenant) else "is_family_head DESC, age DESC, sl_no ASC"

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT
                    voter_id,
                    legacy_id,
                    ward_no,
                    part_no,
                    sl_no,
                    name_hi,
                    name_en,
                    rel_type,
                    rel_name_hi,
                    rel_name_en,
                    house_no,
                    age,
                    gender,
                    epic_no,
                    contact_phone,
                    caste_tag,
                    voter_category,
                    survey_status,
                    is_voted,
                    is_family_head
                FROM voters
                WHERE {where_sql}
                ORDER BY {member_order_sql};
            """, params)
            members = [dict(r) for r in cur.fetchall()]

    display_mukhiya = mukhiya
    if not display_mukhiya and members:
        head_m = next((m for m in members if m.get('is_family_head')), members[0])
        display_mukhiya = head_m.get('name_hi') or head_m.get('name_en') or ''

    mukhiya_en = transliterate_name(display_mukhiya) if any('\u0900' <= c <= '\u097f' for c in display_mukhiya) else display_mukhiya

    return jsonify({
        'success': True,
        'family_id': family_id,
        'mukhiya_name': display_mukhiya,
        'mukhiya_name_en': mukhiya_en,
        'total': len(members),
        'members': members
    })

