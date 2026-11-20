"""
voters.py
Voters Blueprint for Unified Multi-Tenant Voter Platform:
- Single voter detail (/api/voter/<int:voter_id>)
- Family group lookup (/api/family/<family_id>)
- Polling booth voters (/api/voters_by_part/<int:part_no>)

Consumes the data repository via current_app.extensions['repo'].
"""

import re
from flask import Blueprint, jsonify, request, current_app, g, session

voters_bp = Blueprint('voters', __name__)


from electionapp.tenancy import resolve_tenant


@voters_bp.route('/api/voter/<int:voter_id>')
def get_voter(voter_id: int):
    """
    Retrieve single voter by ID.
    Returns 200 with voter dict or 404 if not found.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    voter = repo.get_voter(tenant, voter_id)
    if not voter:
        return jsonify({'success': False, 'error': f'Voter {voter_id} not found'}), 404

    payload = dict(voter)
    payload['success'] = True
    payload['voter'] = voter
    return jsonify(payload), 200


@voters_bp.route('/api/family/<family_id>')
def get_family(family_id: str):
    """
    Retrieve all voters belonging to a given family identifier.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    if hasattr(repo, 'get_family_voters'):
        members = repo.get_family_voters(tenant, str(family_id).strip())
    elif hasattr(repo, 'get_family'):
        members = repo.get_family(tenant, str(family_id).strip())
    else:
        members = []

    return jsonify({
        'success': True,
        'family_id': str(family_id),
        'members': members,
        'count': len(members)
    }), 200


@voters_bp.route('/api/voters_by_ward/<int:part_no>')
@voters_bp.route('/api/voters_by_part/<int:part_no>')
def get_voters_by_part(part_no: int):
    """
    Retrieve voters registered at a specific polling station part number.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    curr_role = session.get('role', '')
    assigned_booths = list(session.get('assigned_booths') or [])
    assigned_booth = session.get('assigned_booth')
    if assigned_booth is not None and assigned_booth not in assigned_booths:
        assigned_booths.append(assigned_booth)

    if assigned_booths and curr_role not in ['super_admin', 'admin']:
        if part_no not in assigned_booths:
            return jsonify({'success': False, 'error': f'Access denied. You are only authorized for booths: {assigned_booths}'}), 403

    limit = request.args.get('limit', default=1000, type=int)
    offset = request.args.get('offset', default=0, type=int)

    voters = repo.get_voters_by_part(tenant, part_no, limit=limit, offset=offset)
    return jsonify({
        'success': True,
        'part_no': part_no,
        'voters': voters,
        'total': len(voters),
        'limit': limit,
        'offset': offset
    }), 200


@voters_bp.route('/api/parts')
def get_parts():
    """
    Retrieve all distinct parts / booths with counts for active tenant.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    if hasattr(repo, 'get_parts'):
        parts = repo.get_parts(tenant)
    else:
        parts = []

    curr_role = session.get('role', '')
    assigned_booths = list(session.get('assigned_booths') or [])
    assigned_booth = session.get('assigned_booth')
    if assigned_booth is not None and assigned_booth not in assigned_booths:
        assigned_booths.append(assigned_booth)

    if assigned_booths and curr_role not in ['super_admin', 'admin']:
        parts = [p for p in parts if p.get('part_no') in assigned_booths]

    return jsonify({
        'success': True,
        'tenant': tenant,
        'parts': parts,
        'total': len(parts)
    }), 200


@voters_bp.route('/api/voters/bundle_version')
def get_bundle_version():
    """Returns lightweight version and campaign size metadata for offline DB sync."""
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500
    
    total_voters = 0
    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                tid, _ = repo._resolve_tenant(tenant)
                cur.execute("SELECT COUNT(*) FROM voters WHERE tenant_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'", (tid,))
                row = cur.fetchone()
                total_voters = row[0] if row else 0
    except Exception:
        total_voters = 0

    supports_full_cache = (0 < total_voters <= 50000)
    return jsonify({
        'success': True,
        'tenant': tenant,
        'version': f"v_{total_voters}",
        'total_voters': total_voters,
        'supports_full_cache': supports_full_cache
    }), 200


@voters_bp.route('/api/voters/campaign_bundle')
def get_campaign_bundle():
    """Returns compact list of voter records for campaigns <= 50,000 voters for automatic 0ms IndexedDB search."""
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    try:
        import psycopg2.extras
        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                tid, _ = repo._resolve_tenant(tenant)
                cur.execute("""
                    SELECT 
                        voter_id,
                        voter_id AS id,
                        COALESCE(sl_no, 0) AS sl_no,
                        COALESCE(part_no, 0) AS part_no,
                        COALESCE(ward_no::text, '') AS ward_no,
                        COALESCE(name_hi, '') AS name_hi,
                        COALESCE(name_en, '') AS name_en,
                        COALESCE(rel_type, '') AS rel_type,
                        COALESCE(rel_name_hi, '') AS rel_name_hi,
                        COALESCE(rel_name_en, '') AS rel_name_en,
                        COALESCE(epic_no, '') AS epic_no,
                        COALESCE(age, 18) AS age,
                        COALESCE(gender, '') AS gender,
                        COALESCE(contact_phone, '') AS contact_phone,
                        COALESCE(caste_tag, '') AS caste_tag,
                        COALESCE(voter_category::text, '0') AS voter_category,
                        COALESCE(is_voted, FALSE) AS is_voted,
                        COALESCE(is_outside, FALSE) AS is_outside,
                        COALESCE(family_id, '') AS family_id,
                        COALESCE(family_head_name, '') AS family_head_name,
                        COALESCE(assigned_karyakarta_name, '') AS assigned_karyakarta_name,
                        COALESCE(house_no, '') AS house_no,
                        COALESCE(booth_area, '') AS booth_area,
                        COALESCE(status, 'Active') AS status
                    FROM voters
                    WHERE tenant_id = %s AND LOWER(COALESCE(status, '')) <> 'deleted'
                    ORDER BY part_no ASC, sl_no ASC
                """, (tid,))
                rows = cur.fetchall()
                resp = jsonify({
                    'success': True,
                    'tenant': tenant,
                    'total': len(rows),
                    'voters': rows
                })
                resp.headers['Cache-Control'] = 'public, max-age=1800, stale-while-revalidate=86400'
                return resp, 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@voters_bp.route('/api/voter/offline_sync', methods=['POST'])
def offline_sync():
    """
    Offline Outbox Synchronization Endpoint.
    Receives batched mutations queued in IndexedDB by field devices when offline.
    Acknowledges synced IDs so the client clears its local outbox.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    data = request.get_json(silent=True) or {}
    mutations = data.get('mutations') or []
    if not mutations:
        return jsonify({'success': True, 'synced_ids': [], 'count': 0}), 200

    synced_ids = []
    tid, _ = repo._resolve_tenant(tenant)

    for item in mutations:
        m_id = item.get('id')
        m_type = item.get('type')
        try:
            if m_type == 'WHATSAPP_DISPATCH':
                phones = item.get('phones') or ([item.get('phone')] if item.get('phone') else [])
                voter_ids = item.get('voter_ids') or []
                slip_type = item.get('slip_type') or 'Single'
                if phones and voter_ids:
                    try:
                        with current_app.test_request_context(
                            '/api/send_openclaw',
                            method='POST',
                            json={
                                'phones': phones,
                                'voter_ids': voter_ids,
                                'slip_type': slip_type
                            },
                            headers={'Host': request.headers.get('Host', '')}
                        ):
                            from electionapp.blueprints.dispatch import api_send_openclaw
                            api_send_openclaw()
                    except Exception as de:
                        current_app.logger.warning(f"[OFFLINE_SYNC_DISPATCH_WARN] {de}")
                if m_id:
                    synced_ids.append(m_id)
            else:
                # Elector profile / notes / tag mutation
                v_id = item.get('voter_id') or item.get('id')
                payload = item.get('payload') or {}
                if isinstance(v_id, str) and not v_id.isdigit():
                    parts = v_id.split('_')
                    if parts and parts[-1].isdigit():
                        v_id = int(parts[-1])
                    else:
                        v_id = None
                else:
                    v_id = int(v_id) if str(v_id).isdigit() else None

                if v_id and payload:
                    set_clauses = []
                    params = []
                    if 'contact_phone' in payload:
                        raw_ph = str(payload.get('contact_phone') or '').strip()
                        ph = re.sub(r'\D', '', raw_ph)
                        if ph.startswith('91') and len(ph) == 12: ph = ph[2:]
                        if ph.startswith('0') and len(ph) == 11: ph = ph[1:]
                        set_clauses.append("contact_phone = %s")
                        params.append(ph if len(ph) == 10 else None)
                    if 'caste_tag' in payload:
                        caste = str(payload.get('caste_tag') or '').strip()
                        set_clauses.append("caste_tag = %s")
                        params.append(caste or None)
                    if 'voter_category' in payload or 'category' in payload:
                        raw_cat = payload.get('voter_category') if 'voter_category' in payload else payload.get('category')
                        set_clauses.append("voter_category = %s")
                        try:
                            params.append(int(raw_cat))
                        except (ValueError, TypeError):
                            params.append(0)
                    if 'is_migrant' in payload or 'is_outside' in payload:
                        is_out = payload.get('is_outside') if 'is_outside' in payload else payload.get('is_migrant')
                        set_clauses.append("is_outside = %s")
                        params.append(bool(is_out))
                    if 'is_voted' in payload:
                        set_clauses.append("is_voted = %s")
                        params.append(bool(payload.get('is_voted')))
                    if 'notes' in payload:
                        set_clauses.append("notes = %s")
                        params.append(str(payload.get('notes') or '').strip())

                    if set_clauses:
                        sql = f"UPDATE voters SET {', '.join(set_clauses)} WHERE tenant_id = %s AND voter_id = %s"
                        params.extend([tid, v_id])
                        with repo.get_connection() as conn:
                            with conn.cursor() as cur:
                                cur.execute(sql, tuple(params))
                            conn.commit()

                if m_id:
                    synced_ids.append(m_id)
        except Exception as ex:
            current_app.logger.error(f"[OFFLINE_SYNC_ERR] Failed processing mutation {m_id}: {ex}")

    return jsonify({
        'success': True,
        'synced_ids': synced_ids,
        'count': len(synced_ids)
    }), 200

