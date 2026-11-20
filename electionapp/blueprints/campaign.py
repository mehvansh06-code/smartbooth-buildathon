"""
campaign.py
Multi-Tenant Campaign Strategy, Political Categorization & WhatsApp Status Blueprint:
- /api/voter/profile/update — Update single voter profile (phone, category, notes, migrant)
- /api/voter/category       — Update single voter political category
- /api/voter/bulk_category  — Bulk update political categories
- /api/warroom/stats        — Realtime war room win projection and booth strength breakdown
- /api/openclaw_status      — Meta WhatsApp Cloud API status
- /api/check_whatsapp       — Validate WhatsApp phone number formatting
"""

import re
from typing import Optional, Union
import psycopg2.extras
from flask import Blueprint, jsonify, request, session

from electionapp.audit import audit_action
from electionapp.tenancy import resolve_tenant, mukhiya_prefers_male
from electionapp.config import is_ward_campaign, load_tenant_config
from electionapp import whatsapp_quota
import meta_whatsapp

campaign_bp = Blueprint('campaign', __name__)


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
# VOTER PROFILE & POLITICAL CATEGORIZATION APIS
# ---------------------------------------------------------------------------

@campaign_bp.route('/api/voter/profile/update', methods=['POST'])
@audit_action('voter.edit', entity_type='voter')
def api_voter_profile_update():
    """
    Unified Voter Profile Tagging API:
    Updates voter contact_phone, caste_tag, voter_category (-1, 0, 50, 100),
    is_outside migrant flag, and records volunteer calling notes.
    """
    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500
    tid, slug = repo._resolve_tenant(tenant)

    data = request.get_json(silent=True) or request.form or {}
    
    # Support both single voter_id and batch voter_ids
    voter_ids_raw = data.get('voter_ids')
    clean_v_ids = []
    if voter_ids_raw and isinstance(voter_ids_raw, (list, tuple)):
        for x in voter_ids_raw:
            try:
                clean_v_ids.append(int(x))
            except (ValueError, TypeError):
                pass

    voter_id = data.get('voter_id') or data.get('id')
    if voter_id:
        try:
            single_id = int(voter_id)
            if single_id not in clean_v_ids:
                clean_v_ids.append(single_id)
        except (ValueError, TypeError):
            pass

    if not clean_v_ids:
        return jsonify({'success': False, 'error': 'At least one Voter ID is required'}), 400

    if session.get('role') == 'viewer':
        return jsonify({'success': False, 'error': 'दर्शक (Viewer) केवल देखने के लिए अधिकृत है। डेटा संपादित करने की अनुमति नहीं है (Viewer role is read-only)'}), 403

    v_id = clean_v_ids[0]

    if session.get('role') == 'karyakarta':
        user_karya_id = session.get('karyakarta_id')
        if not user_karya_id:
            return jsonify({'success': False, 'error': 'सत्र समाप्त या अमान्य (Unauthorized karyakarta session)'}), 403

        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                # Check if any requested voter does not belong to this karyakarta
                cur.execute("""
                    SELECT count(*) FROM voters
                    WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                      AND (assigned_karyakarta_id IS NULL OR assigned_karyakarta_id != %s)
                """, (tid, clean_v_ids, clean_v_ids, user_karya_id))
                unassigned_cnt = cur.fetchone()[0]

                cur.execute("""
                    SELECT count(*) FROM voters
                    WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                """, (tid, clean_v_ids, clean_v_ids))
                matched_cnt = cur.fetchone()[0]

                if matched_cnt == 0 or unassigned_cnt > 0:
                    return jsonify({'success': False, 'error': 'आप केवल अपने आवंटित मतदाताओं को ही संपादित कर सकते हैं (You can only edit your assigned voters)'}), 403

    contact_phone = data.get('contact_phone')
    if contact_phone is not None:
        contact_phone = str(contact_phone).strip()
        m = re.sub(r'\D', '', contact_phone)
        if len(m) == 12 and m.startswith('91'):
            contact_phone = m[2:]
        elif len(m) == 11 and m.startswith('0'):
            contact_phone = m[1:]
        elif len(m) == 10:
            contact_phone = m
        elif not m:
            if len(clean_v_ids) > 1:
                # In bulk mode, empty phone must never wipe existing phones
                contact_phone = None
            else:
                # In single voter mode, clearing phone sets it to empty/null
                contact_phone = ''
        else:
            # Invalid length
            contact_phone = None


    consolidate_family = bool(data.get('consolidate_family', False))

    caste_tag = data.get('caste_tag')
    if caste_tag is not None:
        caste_tag = str(caste_tag).strip()

    voter_category = data.get('voter_category')
    cat_val = _cat_to_int(voter_category) if voter_category is not None else None

    is_outside = data.get('is_outside')
    if is_outside is not None:
        is_outside = 1 if (is_outside in [True, 1, '1', 'true', 'True']) else 0

    survey_status = data.get('survey_status')
    if survey_status is not None:
        survey_status = str(survey_status).strip() if str(survey_status).strip() else None

    notes = data.get('notes')
    family_head_name = data.get('family_head_name')

    assigned_karyakarta_id = data.get('assigned_karyakarta_id')
    karyakarta_name = None
    has_karya = ('assigned_karyakarta_id' in data)

    mukhiya_voter_id = data.get('mukhiya_voter_id')
    try:
        mukhiya_voter_id = int(mukhiya_voter_id) if mukhiya_voter_id is not None else None
    except (ValueError, TypeError):
        mukhiya_voter_id = None

    if session.get('role') == 'karyakarta':
        consolidate_family = False
        has_karya = False
        assigned_karyakarta_id = None
        mukhiya_voter_id = None

    phone_target_mode = data.get('phone_target_mode') or data.get('phone_target') or 'missing_only'
    target_head_id = mukhiya_voter_id if (mukhiya_voter_id and mukhiya_voter_id in clean_v_ids) else None

    if consolidate_family and len(clean_v_ids) > 1:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT family_id FROM voters
                    WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                      AND family_id IS NOT NULL AND family_id != ''
                    LIMIT 1
                """, (tid, clean_v_ids, clean_v_ids))
                row = cur.fetchone()
                assigned_fid = row[0] if (row and row[0]) else f"F_{clean_v_ids[0]}"

                cur.execute("""
                    UPDATE voters
                    SET family_id = %s, family_count = %s, family_source_type = 'MANUAL'
                    WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                """, (assigned_fid, len(clean_v_ids), tid, clean_v_ids, clean_v_ids))

                if target_head_id:
                    # Explicit Mukhiya chosen: reset all members to FALSE, set chosen Mukhiya to TRUE
                    cur.execute("""
                        UPDATE voters SET is_family_head = FALSE
                        WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                    """, (tid, clean_v_ids, clean_v_ids))
                    cur.execute("""
                        UPDATE voters SET is_family_head = TRUE
                        WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                    """, (tid, target_head_id, target_head_id))
                else:
                    # Fallback: if no head exists in group, choose highest age
                    cur.execute("""
                        SELECT count(*) FROM voters
                        WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                          AND is_family_head = TRUE
                    """, (tid, clean_v_ids, clean_v_ids))
                    cnt_head = cur.fetchone()[0]
                    if cnt_head == 0:
                        head_order_sql = (
                            "(gender = 'M') DESC, age DESC NULLS LAST, sl_no ASC"
                            if mukhiya_prefers_male(tenant) else "age DESC NULLS LAST, sl_no ASC"
                        )
                        cur.execute("""
                            UPDATE voters SET is_family_head = TRUE
                            WHERE tenant_id = %s AND voter_id = (
                                SELECT voter_id FROM voters
                                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                                ORDER BY {order} LIMIT 1
                            )
                        """.format(order=head_order_sql), (tid, tid, clean_v_ids, clean_v_ids))
            conn.commit()

    if has_karya and assigned_karyakarta_id:
        try:
            k_int = int(assigned_karyakarta_id)
            if k_int > 0:
                with repo.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT name FROM karyakartas WHERE tenant_id = %s AND karyakarta_id = %s", (tid, k_int))
                    row = cur.fetchone()
                    if row:
                        karyakarta_name = row[0]
                        assigned_karyakarta_id = k_int
                    else:
                        cur.execute("SELECT full_name FROM users WHERE tenant_id = %s AND (user_id = %s OR karyakarta_id = %s)", (tid, k_int, k_int))
                        u_row = cur.fetchone()
                        if u_row:
                            karyakarta_name = u_row[0]
                            assigned_karyakarta_id = k_int
                        else:
                            assigned_karyakarta_id = None
            else:
                assigned_karyakarta_id = None
        except (ValueError, TypeError):
            assigned_karyakarta_id = None
    elif has_karya:
        assigned_karyakarta_id = None

    updates = []
    params = []

    # In bulk mode with missing_only or mukhiya_only, contact_phone is handled separately
    bulk_targeted_phone = (len(clean_v_ids) > 1 and contact_phone and phone_target_mode in ('missing_only', 'mukhiya_only'))

    if contact_phone is not None and not bulk_targeted_phone:
        if contact_phone == '' and len(clean_v_ids) == 1:
            updates.append("contact_phone = NULL")
            updates.append("phone_source_type = NULL")
        else:
            updates.append("contact_phone = %s")
            params.append(contact_phone)
            updates.append("phone_source_type = 'RECONCILED_V2'")

    if caste_tag is not None:
        updates.append("caste_tag = %s")
        params.append(caste_tag)
    if voter_category is not None:
        updates.append("voter_category = %s")
        params.append(cat_val)
        updates.append("category_updated_at = NOW()")
    if is_outside is not None:
        updates.append("is_outside = %s")
        params.append(bool(is_outside))
    if 'survey_status' in data:
        updates.append("survey_status = %s")
        params.append(survey_status)
    if notes is not None:
        updates.append("notes = %s")
        params.append(str(notes).strip() if notes else None)
    if family_head_name is not None:
        updates.append("family_head_name = %s")
        params.append(str(family_head_name).strip() if family_head_name else None)
    if has_karya:
        updates.append("assigned_karyakarta_id = %s")
        params.append(assigned_karyakarta_id)
        updates.append("assigned_karyakarta_name = %s")
        params.append(karyakarta_name)

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            if updates:
                if len(clean_v_ids) == 1:
                    sql = f"UPDATE voters SET {', '.join(updates)} WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)"
                    cur.execute(sql, params + [tid, v_id, v_id])
                else:
                    sql = f"UPDATE voters SET {', '.join(updates)} WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))"
                    cur.execute(sql, params + [tid, clean_v_ids, clean_v_ids])

            # Handle bulk targeted phone updates
            if bulk_targeted_phone:
                if phone_target_mode == 'missing_only':
                    cur.execute("""
                        UPDATE voters
                        SET contact_phone = %s, phone_source_type = 'RECONCILED_V2_KINSHIP', updated_at = NOW()
                        WHERE tenant_id = %s
                          AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                          AND (contact_phone IS NULL OR trim(contact_phone) = '')
                    """, (contact_phone, tid, clean_v_ids, clean_v_ids))
                elif phone_target_mode == 'mukhiya_only':
                    head_target = target_head_id or clean_v_ids[0]
                    cur.execute("""
                        UPDATE voters
                        SET contact_phone = %s, phone_source_type = 'RECONCILED_V2', updated_at = NOW()
                        WHERE tenant_id = %s
                          AND (voter_id = %s OR legacy_id = %s)
                    """, (contact_phone, tid, head_target, head_target))

            if is_outside is not None:
                if is_outside:
                    cur.execute("""
                        INSERT INTO outside_voters
                            (tenant_id, voter_id, part_no, sl_no, name_en, name_hi,
                             rel_name_en, rel_name_hi, rel_type, house_no, epic_no,
                             age, gender, contact_phone, current_city, notes, marked_at)
                        SELECT v.tenant_id, v.voter_id, v.part_no, v.sl_no, v.name_en, v.name_hi,
                               v.rel_name_en, v.rel_name_hi, v.rel_type, v.house_no, v.epic_no,
                               v.age, v.gender, COALESCE(NULLIF(%s, ''), v.contact_phone, ''), '', %s, NOW()
                        FROM voters v
                        WHERE v.tenant_id = %s AND (v.voter_id = ANY(%s) OR v.legacy_id = ANY(%s))
                        ON CONFLICT (tenant_id, voter_id) DO UPDATE SET
                            contact_phone = CASE
                                WHEN EXCLUDED.contact_phone != '' THEN EXCLUDED.contact_phone
                                ELSE outside_voters.contact_phone
                            END,
                            notes = CASE
                                WHEN EXCLUDED.notes != '' THEN EXCLUDED.notes
                                ELSE outside_voters.notes
                            END,
                            marked_at = NOW()
                    """, (contact_phone or '', notes or '', tid, clean_v_ids, clean_v_ids))
                else:
                    cur.execute("""
                        DELETE FROM outside_voters
                        WHERE tenant_id = %s AND (
                            voter_id = ANY(%s) OR voter_id IN (
                                SELECT voter_id FROM voters WHERE tenant_id = %s AND legacy_id = ANY(%s)
                            )
                        )
                    """, (tid, clean_v_ids, tid, clean_v_ids))
            conn.commit()

    return jsonify({
        'success': True,
        'voter_id': v_id if len(clean_v_ids) == 1 else None,
        'voter_ids': clean_v_ids,
        'count': len(clean_v_ids),
        'updated': {
            'contact_phone': (None if (contact_phone == '' and len(clean_v_ids) == 1) else contact_phone),
            'caste_tag': caste_tag,
            'voter_category': cat_val,
            'is_outside': is_outside,
            'survey_status': survey_status,
            'assigned_karyakarta_id': assigned_karyakarta_id,
            'assigned_karyakarta_name': karyakarta_name,
            'family_head_name': family_head_name,
            'notes': notes
        }
    })


@campaign_bp.route('/api/voter/category', methods=['POST'])
@audit_action('voter.tag', entity_type='voter')
def api_voter_category():
    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500
    tid, slug = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_id = data.get('voter_id') or data.get('id')
    category = data.get('category')

    if not voter_id:
        return jsonify({'success': False, 'error': 'Voter ID is required'}), 400

    try:
        v_id = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': 'Invalid voter ID'}), 400

    if session.get('role') == 'viewer':
        return jsonify({'success': False, 'error': 'दर्शक (Viewer) केवल देखने के लिए अधिकृत है। डेटा संपादित करने की अनुमति नहीं है (Viewer role is read-only)'}), 403

    cat_val = _cat_to_int(category)

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET voter_category = %s,
                    category_updated_at = NOW()
                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
            """, (cat_val, tid, v_id, v_id))
        conn.commit()

    return jsonify({'success': True, 'voter_id': v_id, 'category': category})


@campaign_bp.route('/api/voter/bulk_category', methods=['POST'])
@audit_action('voter.bulk_tag', entity_type='voter')
def api_voter_bulk_category():
    if session.get('role') == 'viewer':
        return jsonify({'success': False, 'error': 'दर्शक (Viewer) केवल देखने के लिए अधिकृत है। डेटा संपादित करने की अनुमति नहीं है (Viewer role is read-only)'}), 403
    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500
    tid, slug = repo._resolve_tenant(tenant)

    data = request.get_json(force=True) or {}
    voter_ids = data.get('voter_ids', [])
    category = data.get('category')

    if not voter_ids:
        return jsonify({'success': False, 'error': 'No voter IDs provided'}), 400

    clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
    if not clean_ids:
        return jsonify({'success': False, 'error': 'Invalid voter IDs'}), 400

    cat_val = _cat_to_int(category)
    count = 0

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE voters
                SET voter_category = %s,
                    category_updated_at = NOW()
                WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
            """, (cat_val, tid, clean_ids, clean_ids))
            count = cur.rowcount
        conn.commit()

    return jsonify({'success': True, 'count': count, 'category': category})


# ---------------------------------------------------------------------------
# WAR ROOM STRATEGY & METRICS
# ---------------------------------------------------------------------------

@campaign_bp.route('/api/warroom/stats')
def api_warroom_stats():
    tenant, repo = _get_tenant()
    tid, slug = repo._resolve_tenant(tenant)
    slug = slug or tenant

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    count(*) as total,
                    sum(case when voter_category = 100 then 1 else 0 end) as favored_100,
                    sum(case when voter_category = 50 then 1 else 0 end) as swing_50,
                    sum(case when voter_category = -1 then 1 else 0 end) as opposition,
                    sum(case when voter_category IS NULL OR voter_category = 0 then 1 else 0 end) as unclassified,
                    sum(case when is_voted = true then 1 else 0 end) as voted,
                    sum(case when voter_category = 100 and is_voted = true then 1 else 0 end) as voted_favored,
                    sum(case when voter_category = 50 and is_voted = true then 1 else 0 end) as voted_swing,
                    sum(case when voter_category = -1 and is_voted = true then 1 else 0 end) as voted_opp,
                    max(voted_at) as last_voted_at
                FROM voters
                WHERE tenant_id = %s
                  -- struck-off electors (विलोपन) are not electors; exclude from counts
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
            """, (tid,))
            row = dict(cur.fetchone() or {})
            total = row.get('total') or 0
            favored = row.get('favored_100') or 0
            swing = row.get('swing_50') or 0
            opp = row.get('opposition') or 0
            unclass = row.get('unclassified') or 0
            voted = row.get('voted') or 0
            voted_favored = row.get('voted_favored') or 0
            voted_swing = row.get('voted_swing') or 0
            voted_opp = row.get('voted_opp') or 0
            last_voted_at = row.get('last_voted_at')

            turnout_pct = round((voted / total * 100), 1) if total > 0 else 0.0
            pending_favored_gotv = favored - voted_favored
            if last_voted_at:
                last_voted_at = str(last_voted_at)[:19]

            projected_votes = round(favored + (0.50 * swing) + (0.30 * unclass))
            projected_opp_votes = round(opp + (0.50 * swing) + (0.30 * unclass))
            projected_lead = projected_votes - projected_opp_votes

            try:
                cfg = load_tenant_config(slug)
            except Exception:
                cfg = {}
            is_ward_election = is_ward_campaign(cfg)

            cur.execute("""
                SELECT count(DISTINCT ward_no) as ward_count
                FROM voters
                WHERE tenant_id = %s
                  AND ward_no IS NOT NULL
                  AND trim(ward_no::text) NOT IN ('', '0')
                  AND LOWER(COALESCE(status, '')) <> 'deleted'
            """, (tid,))
            w_res = cur.fetchone()
            distinct_wards = (dict(w_res).get('ward_count') if w_res else 0) or 0
            use_ward_grouping = is_ward_election or (distinct_wards > 1)

            if use_ward_grouping:
                cur.execute("""
                    SELECT
                        ward_no as part_no,
                        COALESCE(MIN(booth_name), 'वार्ड ' || ward_no::text) as booth_name,
                        count(*) as total,
                        sum(case when voter_category = 100 then 1 else 0 end) as favored_100,
                        sum(case when voter_category = 50 then 1 else 0 end) as swing_50,
                        sum(case when voter_category = -1 then 1 else 0 end) as opposition,
                        sum(case when voter_category IS NULL OR voter_category = 0 then 1 else 0 end) as unclassified,
                        sum(case when is_voted = true then 1 else 0 end) as voted,
                        sum(case when voter_category = 100 and is_voted = true then 1 else 0 end) as voted_favored,
                        sum(case when voter_category = 50 and is_voted = true then 1 else 0 end) as voted_swing,
                        sum(case when voter_category = -1 and is_voted = true then 1 else 0 end) as voted_opp,
                        max(voted_at) as last_voted_at
                    FROM voters
                    WHERE tenant_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY ward_no
                    ORDER BY
                        CASE WHEN ward_no::text ~ '^[0-9]+$' THEN (ward_no::text)::integer ELSE 999999 END,
                        ward_no
                """, (tid,))
            else:
                cur.execute("""
                    SELECT
                        part_no,
                        COALESCE(booth_name, '') as booth_name,
                        count(*) as total,
                        sum(case when voter_category = 100 then 1 else 0 end) as favored_100,
                        sum(case when voter_category = 50 then 1 else 0 end) as swing_50,
                        sum(case when voter_category = -1 then 1 else 0 end) as opposition,
                        sum(case when voter_category IS NULL OR voter_category = 0 then 1 else 0 end) as unclassified,
                        sum(case when is_voted = true then 1 else 0 end) as voted,
                        sum(case when voter_category = 100 and is_voted = true then 1 else 0 end) as voted_favored,
                        sum(case when voter_category = 50 and is_voted = true then 1 else 0 end) as voted_swing,
                        sum(case when voter_category = -1 and is_voted = true then 1 else 0 end) as voted_opp,
                        max(voted_at) as last_voted_at
                    FROM voters
                    WHERE tenant_id = %s
                      AND LOWER(COALESCE(status, '')) <> 'deleted'
                    GROUP BY part_no, booth_name
                    ORDER BY part_no
                """, (tid,))
            booths = []
            booth_breakdown = []
            for b in cur.fetchall():
                bd = dict(b)
                b_tot = bd.get('total') or 1
                b_fav = bd.get('favored_100') or 0
                b_swg = bd.get('swing_50') or 0
                b_opp = bd.get('opposition') or 0
                b_unc = bd.get('unclassified') or 0
                b_voted = bd.get('voted') or 0
                b_voted_favored = bd.get('voted_favored') or 0
                b_voted_swing = bd.get('voted_swing') or 0
                b_voted_opp = bd.get('voted_opp') or 0
                bd['voted'] = b_voted
                bd['voted_favored'] = b_voted_favored
                bd['voted_swing'] = b_voted_swing
                bd['voted_opp'] = b_voted_opp
                bd['turnout_pct'] = round((b_voted / b_tot * 100), 1) if b_tot > 0 else 0.0
                bd['pending_favored_gotv'] = b_fav - b_voted_favored
                if bd.get('last_voted_at'):
                    bd['last_voted_at'] = str(bd['last_voted_at'])[:19]
                if is_ward_election:
                    # Pure Ward Number for Zila Parishad, Panchayat Samiti, Sarpanch, and Municipal elections
                    b_name = f"वार्ड {bd.get('part_no')}"
                elif use_ward_grouping:
                    b_name = f"वार्ड {bd.get('part_no')}"
                    if bd.get('booth_name') and bd.get('booth_name') != b_name:
                        b_name += f" ({bd.get('booth_name')})"
                else:
                    b_name = bd.get('booth_name') or f"भाग {bd.get('part_no')}"
                bd['name'] = b_name
                bd['fav_pct'] = round((b_fav / b_tot * 100), 1)
                if bd['fav_pct'] >= 50:
                    bd['strength'] = "Stronghold (मजबूत किला)"
                    bd['badge_color'] = "emerald"
                elif bd['fav_pct'] >= 35:
                    bd['strength'] = "Battleground (प्रतिस्पर्धा)"
                    bd['badge_color'] = "amber"
                else:
                    bd['strength'] = "Deficit (सघन कार्य आवश्यक)"
                    bd['badge_color'] = "rose"
                booths.append(bd)
                booth_breakdown.append({
                    'part_no': bd.get('part_no'),
                    'name': b_name,
                    'total': b_tot,
                    'favored': b_fav,
                    'voted_favored': b_voted_favored,
                    'swing': b_swg,
                    'voted_swing': b_voted_swing,
                    'opp': b_opp,
                    'voted_opp': b_voted_opp,
                    'unclass': b_unc,
                    'fav_pct': bd['fav_pct'],
                    'voted': b_voted,
                    'turnout_pct': bd['turnout_pct'],
                    'pending_favored_gotv': bd['pending_favored_gotv'],
                    'last_voted_at': bd.get('last_voted_at')
                })

    return jsonify({
        "success": True,
        "is_ward_election": is_ward_election,
        "total_electorate": total,
        "total_voters": total,
        "favored_count": favored,
        "voted_favored_count": voted_favored,
        "swing_count": swing,
        "voted_swing_count": voted_swing,
        "opposition_count": opp,
        "voted_opposition_count": voted_opp,
        "unclassified_count": unclass,
        "projected_votes": projected_votes,
        "projected_opp_votes": projected_opp_votes,
        "projected_lead": projected_lead,
        "booth_breakdown": booth_breakdown,
        "sentiment": {
            "total": total,
            "favored_100": favored,
            "swing_50": swing,
            "opposition": opp,
            "unclassified": unclass,
            "favored_pct": round((favored / total * 100), 1) if total > 0 else 0,
            "swing_pct": round((swing / total * 100), 1) if total > 0 else 0,
            "opposition_pct": round((opp / total * 100), 1) if total > 0 else 0
        },
        "projection": {
            "projected_votes": projected_votes,
            "projected_opp_votes": projected_opp_votes,
            "projected_lead": projected_lead
        },
        "turnout": {
            "voted": voted,
            "turnout_pct": turnout_pct,
            "pending_favored_gotv": pending_favored_gotv,
            "last_voted_at": last_voted_at
        },
        "booths": booths
    })


# ---------------------------------------------------------------------------
# WHATSAPP / META API STATUS
# ---------------------------------------------------------------------------

@campaign_bp.route('/api/openclaw_status')
def api_openclaw_status():
    meta_health = meta_whatsapp.check_health()
    is_connected = bool(meta_health.get('connected'))
    return jsonify({
        'provider': 'meta_cloud_api',
        'status': 'online' if is_connected else 'offline',
        'is_logged_in': is_connected,
        'has_qr': False,
        'display_number': meta_health.get('display_number', '+91 73001 79679'),
        'verified_name': meta_health.get('verified_name', 'Digital Prachar Wala'),
        'messaging_limit': meta_health.get('messaging_limit', 'TIER_10K'),
        'quality_rating': meta_health.get('quality_rating', 'GREEN'),
        'lines': {
            'meta': {
                'connected': is_connected,
                'status': 'online' if is_connected else 'offline',
                'port': 'cloud',
                'name': 'Meta Official Cloud API',
                'display_number': meta_health.get('display_number', '+91 73001 79679'),
                'tier': meta_health.get('messaging_limit', 'TIER_10K'),
                'rating': meta_health.get('quality_rating', 'GREEN')
            }
        }
    }), 200


@campaign_bp.route('/api/campaign/whatsapp_quota')
def api_campaign_whatsapp_quota():
    """Read-only WhatsApp credit balance for the current campaign.

    Shown to campaign operators so they know why a dispatch was refused. Writes live
    only on the super-admin blueprint; there is deliberately no setter here. The
    tenant comes from the authoritative resolver, not from a request parameter.
    """
    tenant, _repo = resolve_tenant()
    try:
        quota = whatsapp_quota.get_quota(tenant)
    except whatsapp_quota.QuotaError as qe:
        return jsonify({'success': False, 'error': str(qe)}), 503

    # Never cache a credit balance; a stale number is read as the live spend limit.
    resp = jsonify({
        'success': True,
        'campaign': tenant,
        'granted': quota['granted'],
        'consumed': quota['consumed'],
        'available': quota['available'],
        'is_blocked': quota['is_blocked'],
        'can_send': quota['can_send'],
        'configured': quota['configured'],
        'price_per_msg_paise': quota.get('price_per_msg_paise', 25),
        'price_per_msg_inr': quota.get('price_per_msg_inr', 0.25),
    })
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp, 200


@campaign_bp.route('/api/check_whatsapp')
def api_check_whatsapp():
    phone = request.args.get('phone', '').strip()
    if not phone or len(phone) < 10:
        return jsonify({'success': False, 'error': 'Invalid phone number'}), 400

    mobiles = meta_whatsapp.extract_all_mobile_numbers(phone)
    if not mobiles:
        return jsonify({'success': False, 'error': 'No valid mobile number found'}), 400

    return jsonify({
        'success': True,
        'phone': mobiles[0],
        'status': 'valid_format',
        'provider': 'meta_cloud_api'
    }), 200
