"""
turnout.py
Election Day Voting, Turnout Tracking, Quick Mark & GOTV Targeting Blueprint:
- /api/vote/toggle, /api/turnout/mark_single — Toggle single voter's turnout status
- /api/vote/bulk_mark                       — Mark voter list as voted/unvoted
- /api/vote/quick_mark                      — Rapid booth keypad marking (part + serial)
- /api/vote/stats, /api/turnout/stats       — Live election day turnout summary & breakdown
- /api/vote/gotv, /api/turnout/gotv_pending — Get Out The Vote (GOTV) targeting list
"""

import re
import datetime
from typing import Optional, Union
import psycopg2.extras
from flask import Blueprint, jsonify, request, session

from electionapp.audit import audit_action
from electionapp.tenancy import resolve_tenant
from electionapp.config import parse_location_filter

turnout_bp = Blueprint('turnout', __name__)


def _get_tenant():
    """Resolve active tenant slug and repo using authoritative tenancy resolver."""
    return resolve_tenant()


def _cat_to_int(cat_str: Optional[Union[str, int]]) -> int:
    if not cat_str or str(cat_str) in ('0', 'unmarked', 'all'):
        return 0
    if str(cat_str) == '100':
        return 100
    if str(cat_str) in ('50_50', '50'):
        return 50
    if str(cat_str) in ('opposition', '-1'):
        return -1
    try:
        return int(cat_str)
    except Exception:
        return 0


def _int_to_cat(val: Optional[int]) -> str:
    if val == 100:
        return '100'
    if val == 50:
        return '50_50'
    if val == -1:
        return 'opposition'
    return 'unmarked'


# ---------------------------------------------------------------------------
# VOTING & TURNOUT MARKING APIS
# ---------------------------------------------------------------------------

@turnout_bp.route('/api/vote/toggle', methods=['POST'])
@turnout_bp.route('/api/turnout/mark_single', methods=['POST'])
@audit_action('voter.mark_voted', entity_type='voter')
def api_vote_toggle():
    if session.get('role') == 'karyakarta':
        return jsonify({'success': False, 'error': 'कार्यकर्ता को वोट मार्क करने की अनुमति नहीं है (Restricted: Karyakartas cannot mark votes)'}), 403

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_id = data.get('voter_id') or data.get('id')
    is_voted_param = data.get('is_voted')
    session_user = session.get('full_name') or session.get('username') or 'Booth Agent'
    marked_by = data.get('marked_by') or session_user

    if not voter_id:
        return jsonify({'success': False, 'error': 'Voter ID is required'}), 400

    try:
        v_id = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter ID'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, part_no, sl_no, is_voted, name_en, name_hi
                FROM voters
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                LIMIT 1
            """, (tid, v_id, v_id))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Voter ID {voter_id} not found'}), 404

            current_status = 1 if row.get('is_voted') else 0
            if is_voted_param is None:
                new_status = 0 if current_status == 1 else 1
            else:
                new_status = 1 if is_voted_param else 0

            now_ts = datetime.datetime.now()
            marked_by_id = int(marked_by) if str(marked_by).isdigit() else (session.get('user_id') or session.get('karyakarta_id') or 1)
            cur.execute("""
                UPDATE voters
                SET is_voted = %s,
                    voted_at = %s,
                    voted_marked_by = %s
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
            """, (bool(new_status), now_ts if new_status else None, marked_by_id if new_status else None, tid, v_id, v_id))

        conn.commit()

    return jsonify({
        'success': True,
        'voter_id': v_id,
        'is_voted': new_status,
        'voted_at': now_ts.strftime('%Y-%m-%d %H:%M:%S') if new_status else None,
        'marked_by': marked_by if new_status else None,
        'voter_name': row.get('name_en') or row.get('name_hi') or ''
    })


@turnout_bp.route('/api/vote/bulk_mark', methods=['POST'])
@audit_action('voter.bulk_mark_voted', entity_type='voter')
def api_vote_bulk_mark():
    if session.get('role') == 'karyakarta':
        return jsonify({'success': False, 'error': 'कार्यकर्ता को वोट मार्क करने की अनुमति नहीं है (Restricted: Karyakartas cannot mark votes)'}), 403

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_ids = data.get('voter_ids', [])
    is_voted = data.get('is_voted', 1)
    marked_by = data.get('marked_by') or 'Booth Agent'

    if not voter_ids:
        return jsonify({'success': False, 'error': 'No voter IDs provided'}), 400

    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return jsonify({'success': False, 'error': 'Invalid voter IDs'}), 400

    new_status = bool(is_voted)
    now_ts = datetime.datetime.now()
    marked_by_id = int(marked_by) if str(marked_by).isdigit() else 1

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET is_voted = %s,
                    voted_at = %s,
                    voted_marked_by = %s
                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
            """, (new_status, now_ts if new_status else None, marked_by_id if new_status else None, tid, clean_ids, clean_ids))
            count = cur.rowcount
        conn.commit()

    return jsonify({
        'success': True,
        'count': count,
        'is_voted': 1 if new_status else 0,
        'voted_at': now_ts.strftime('%Y-%m-%d %H:%M:%S') if new_status else None
    })


@turnout_bp.route('/api/vote/quick_mark', methods=['POST'])
def api_vote_quick_mark():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    part_no = data.get('part_no') or data.get('part')
    sl_no = data.get('sl_no') or data.get('serial')
    is_voted = data.get('is_voted', 1)
    marked_by = data.get('marked_by') or 'Booth Keypad'

    if not part_no or not sl_no:
        return jsonify({'success': False, 'error': 'part_no and sl_no are required'}), 400

    try:
        p_num = int(part_no)
        s_num = int(sl_no)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'part_no and sl_no must be numbers'}), 400

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT voter_id, name_en, name_hi, house_no, is_voted
                FROM voters
                WHERE tenant_id = %s AND part_no = %s AND sl_no = %s
                LIMIT 1
            """, (tid, p_num, s_num))
            row = cur.fetchone()
            if not row:
                return jsonify({'success': False, 'error': f'Voter Part {part_no} Sl #{sl_no} not found'}), 404

            v_id = row['voter_id']
            now_ts = datetime.datetime.now()
            new_status = bool(is_voted)
            marked_by_id = int(marked_by) if str(marked_by).isdigit() else 1

            cur.execute("""
                UPDATE voters
                SET is_voted = %s,
                    voted_at = %s,
                    voted_marked_by = %s
                WHERE tenant_id = %s AND voter_id = %s
            """, (new_status, now_ts if new_status else None, marked_by_id if new_status else None, tid, v_id))
        conn.commit()

    return jsonify({
        'success': True,
        'voter_id': v_id,
        'is_voted': 1 if new_status else 0,
        'voted_at': now_ts.strftime('%Y-%m-%d %H:%M:%S') if new_status else None,
        'marked_by': marked_by if new_status else None,
        'voter_details': {
            'id': v_id,
            'part_no': p_num,
            'sl_no': s_num,
            'name': row.get('name_en') or row.get('name_hi') or '',
            'house_no': row.get('house_no', '')
        }
    })


@turnout_bp.route('/api/vote/stats')
@turnout_bp.route('/api/turnout/stats')
def api_vote_stats():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # 1. Overall stats
            cur.execute("""
                SELECT 
                    count(*) as total,
                    sum(case when is_voted = true then 1 else 0 end) as voted,
                    sum(case when is_voted = true and (gender = 'स्त्री' or gender = 'Female') then 1 else 0 end) as voted_female,
                    sum(case when is_voted = true and (gender != 'स्त्री' and gender != 'Female') then 1 else 0 end) as voted_male,
                    sum(case when (gender = 'स्त्री' or gender = 'Female') then 1 else 0 end) as total_female,
                    sum(case when (gender != 'स्त्री' and gender != 'Female') then 1 else 0 end) as total_male
                FROM voters
                WHERE tenant_id = %s
                  -- struck-off electors (विलोपन) are not electors; exclude from counts
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
            """, (tid,))
            ov = dict(cur.fetchone() or {})
            total = ov.get('total') or 0
            voted = ov.get('voted') or 0
            pending = total - voted
            pct = round((voted / total * 100), 1) if total > 0 else 0.0

            # 2. Ward-by-ward (Panchayat) or Booth-by-booth stats
            hierarchy = repo.get_panchayat_hierarchy(tid) if hasattr(repo, 'get_panchayat_hierarchy') else {}
            is_multi_gp = bool(hierarchy.get('is_multi_gp'))

            if is_multi_gp:
                cur.execute("""
                    SELECT 
                        part_no,
                        ward_no,
                        COALESCE(gram_panchayat, '') as gram_panchayat,
                        MIN(COALESCE(booth_name, '')) as booth_name,
                        count(*) as total,
                        sum(case when is_voted = true then 1 else 0 end) as voted,
                        max(voted_at) as last_voted_at
                    FROM voters
                    WHERE tenant_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                      AND ward_no IS NOT NULL AND ward_no > 0
                    GROUP BY part_no, ward_no, gram_panchayat
                    ORDER BY part_no, ward_no
                """, (tid,))
                booths = []
                gp_hi_map = {p['gp_name']: p['gp_name_hi'] for p in hierarchy.get('panchayats', [])}
                for r in cur.fetchall():
                    b_dict = dict(r)
                    b_tot = b_dict.get('total') or 0
                    b_vod = b_dict.get('voted') or 0
                    b_dict['pending'] = b_tot - b_vod
                    b_dict['turnout_pct'] = round((b_vod / b_tot * 100), 1) if b_tot > 0 else 0.0
                    w_num = b_dict.get('ward_no')
                    p_num = b_dict.get('part_no')
                    raw_gp = b_dict.get('gram_panchayat') or ''
                    gp_hi = gp_hi_map.get(raw_gp) or raw_gp
                    b_dict['name'] = f"{gp_hi} — वार्ड {w_num}" if gp_hi else f"वार्ड {w_num} (बूथ {p_num})"
                    b_dict['booth_no'] = str(p_num)
                    b_dict['part_no'] = p_num
                    b_dict['ward_no'] = w_num
                    b_dict['is_ward'] = True
                    b_dict['is_multi_gp'] = True
                    b_dict['filter_value'] = f"part:{p_num}_ward:{w_num}"
                    if b_dict.get('last_voted_at'):
                        b_dict['last_voted_at'] = str(b_dict['last_voted_at'])[:19]
                    booths.append(b_dict)
            else:
                cur.execute("""
                    SELECT count(DISTINCT ward_no) AS cnt
                    FROM voters 
                    WHERE tenant_id = %s AND ward_no IS NOT NULL AND ward_no > 0
                """, (tid,))
                row_w = cur.fetchone() or {}
                ward_count = (row_w.get('cnt') if isinstance(row_w, dict) else row_w[0]) or 0
                is_multi_ward = ward_count > 1

                if is_multi_ward:
                    cur.execute("""
                        SELECT 
                            ward_no,
                            MIN(part_no) as part_no,
                            MIN(COALESCE(booth_name, '')) as booth_name,
                            count(*) as total,
                            sum(case when is_voted = true then 1 else 0 end) as voted,
                            max(voted_at) as last_voted_at
                        FROM voters
                        WHERE tenant_id = %s
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                          AND ward_no IS NOT NULL AND ward_no > 0
                        GROUP BY ward_no
                        ORDER BY ward_no
                    """, (tid,))
                    booths = []
                    for r in cur.fetchall():
                        b_dict = dict(r)
                        b_tot = b_dict.get('total') or 0
                        b_vod = b_dict.get('voted') or 0
                        b_dict['pending'] = b_tot - b_vod
                        b_dict['turnout_pct'] = round((b_vod / b_tot * 100), 1) if b_tot > 0 else 0.0
                        w_num = b_dict.get('ward_no')
                        p_num = b_dict.get('part_no')
                        b_dict['name'] = f"वार्ड {w_num}" + (f" (बूथ {p_num})" if p_num else "")
                        b_dict['booth_no'] = str(w_num)
                        b_dict['part_no'] = w_num
                        b_dict['is_ward'] = True
                        if b_dict.get('last_voted_at'):
                            b_dict['last_voted_at'] = str(b_dict['last_voted_at'])[:19]
                        booths.append(b_dict)
                else:
                    cur.execute("""
                        SELECT 
                            part_no,
                            COALESCE(booth_name, '') as booth_name,
                            count(*) as total,
                            sum(case when is_voted = true then 1 else 0 end) as voted,
                            max(voted_at) as last_voted_at
                        FROM voters
                        WHERE tenant_id = %s
                          AND LOWER(COALESCE(status, '')) <> 'deleted'
                        GROUP BY part_no, booth_name
                        ORDER BY part_no
                    """, (tid,))
                    booths = []
                    for r in cur.fetchall():
                        b_dict = dict(r)
                        b_tot = b_dict.get('total') or 0
                        b_vod = b_dict.get('voted') or 0
                        b_dict['pending'] = b_tot - b_vod
                        b_dict['turnout_pct'] = round((b_vod / b_tot * 100), 1) if b_tot > 0 else 0.0
                        b_name = b_dict.get('booth_name') or f"भाग {b_dict.get('part_no')}"
                        b_dict['name'] = b_name
                        m = re.match(r'^(\d+)\s*[-:]', b_name)
                        b_dict['booth_no'] = m.group(1) if m else str(b_dict.get('part_no'))
                        if b_dict.get('last_voted_at'):
                            b_dict['last_voted_at'] = str(b_dict['last_voted_at'])[:19]
                        booths.append(b_dict)

            # 3. Political Category turnout
            cur.execute("""
                SELECT 
                    coalesce(voter_category, 0) as cat,
                    count(*) as total,
                    sum(case when is_voted = true then 1 else 0 end) as voted
                FROM voters
                WHERE tenant_id = %s
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
                GROUP BY coalesce(voter_category, 0)
            """, (tid,))
            cat_turnout = {}
            for r in cur.fetchall():
                c_name = _int_to_cat(r['cat'])
                c_tot = r['total'] or 0
                c_vod = r['voted'] or 0
                cat_turnout[c_name] = {
                    'total': c_tot,
                    'voted': c_vod,
                    'pending': c_tot - c_vod,
                    'turnout_pct': round((c_vod / c_tot * 100), 1) if c_tot > 0 else 0.0
                }

            # 4. Hourly voting pulse
            cur.execute("""
                SELECT 
                    to_char(voted_at, 'HH24:00') as hr,
                    count(*) as count
                FROM voters
                WHERE tenant_id = %s AND is_voted = true AND voted_at IS NOT NULL
                GROUP BY to_char(voted_at, 'HH24:00')
                ORDER BY hr
            """, (tid,))
            hourly = [dict(r) for r in cur.fetchall() if r.get('hr')]

            v_male = ov.get('voted_male') or 0
            v_female = ov.get('voted_female') or 0
            favored_info = cat_turnout.get('100') or {
                'total': 0, 'voted': 0, 'pending': 0, 'turnout_pct': 0.0
            }

    return jsonify({
        "success": True,
        "total_voters": total,
        "total_voted": voted,
        "total_pending": pending,
        "overall_turnout_percent": pct,
        "stats": {
            "total_voters": total,
            "voted_total": voted,
            "turnout_pct": pct,
            "voted_male": v_male,
            "voted_female": v_female,
            "total_male": ov.get('total_male') or 0,
            "total_female": ov.get('total_female') or 0,
            "pending": pending
        },
        "overall": {
            "total": total,
            "voted": voted,
            "pending": pending,
            "turnout_pct": pct,
            "voted_male": v_male,
            "total_male": ov.get('total_male') or 0,
            "voted_female": v_female,
            "total_female": ov.get('total_female') or 0
        },
        "voted_by_gender": {
            "M": v_male,
            "F": v_female
        },
        "favored_stats": favored_info,
        "booths": booths,
        "categories": cat_turnout,
        "hourly": hourly
    })


@turnout_bp.route('/api/vote/gotv')
@turnout_bp.route('/api/turnout/gotv_pending')
def api_vote_gotv():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)

    part_no = request.args.get('part') or request.args.get('part_no')
    ward_no = request.args.get('ward') or request.args.get('ward_no')
    category = request.args.get('category', '100')
    limit = request.args.get('limit', 200, type=int)

    conditions = ["tenant_id = %s", "is_voted = false",
                  "LOWER(COALESCE(status, '')) <> 'deleted'"]
    params = [tid]

    clean_ward, clean_part, clean_parts = parse_location_filter(ward_no, part_no)

    if clean_ward and clean_part:
        conditions.append("ward_no = %s AND part_no = %s")
        params.extend([clean_ward, clean_part])
    elif clean_parts:
        conditions.append("part_no = ANY(%s)")
        params.append(clean_parts)
    elif clean_part:
        conditions.append("part_no = %s")
        params.append(clean_part)
    elif clean_ward:
        conditions.append("ward_no = %s")
        params.append(clean_ward)

    if category and category != 'all':
        conditions.append("voter_category = %s")
        params.append(_cat_to_int(category))

    params.append(min(limit, 1000))

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"""
                SELECT * FROM voters
                WHERE {' AND '.join(conditions)}
                ORDER BY COALESCE(NULLIF(ward_no, 0), part_no, 1) ASC, part_no ASC, sl_no ASC
                LIMIT %s
            """, params)
            voters = [repo._normalize_row(dict(r), tid) for r in cur.fetchall()]

    return jsonify({
        'success': True,
        'category': category,
        'part_no': part_no,
        'count': len(voters),
        'voters': voters
    })
