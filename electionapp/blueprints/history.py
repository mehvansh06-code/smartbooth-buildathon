"""
history.py
Multi-Tenant History, Outside Voters & Phone Update Blueprint.

Routes:
  GET  /api/history                  — dispatch history with search + batch grouping
  POST /api/history/log              — log a manual dispatch
  GET  /api/history/export           — export to Excel
  POST /api/history/delete           — delete history records by id/batch_id
  POST /api/history/edit             — edit phone/notes/delivery_status on a record
  GET  /api/outside_voters           — list outside voters grouped into families
  POST /api/outside_voters/mark      — mark voter(s) as outside
  POST /api/outside_voters/unmark    — unmark outside voter(s)
  GET  /api/outside_voters/export    — export outside voters to Excel
  POST /api/voter/update_phone       — update contact_phone for an individual voter
"""

import io
import re
import time
import random
from datetime import datetime

import psycopg2.extras
from flask import Blueprint, jsonify, request, current_app, g, send_file, session

from electionapp.audit import audit_action
from electionapp.config import load_tenant_config

history_bp = Blueprint('history', __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from electionapp.tenancy import resolve_tenant

def _get_tenant():
    """Resolve active tenant slug and repo from authoritative tenancy resolver."""
    return resolve_tenant()


def _normalize_phone(raw: str) -> str:
    """Strip to 10-digit Indian mobile number or return empty string."""
    digits = re.sub(r'\D', '', raw or '')
    if digits.startswith('91') and len(digits) == 12 and digits[2] in '6789':
        digits = digits[2:]
    elif digits.startswith('0') and len(digits) == 11 and digits[1] in '6789':
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in '6789':
        return digits
    return ''


def _log_dispatch(repo, tenant, phone, voter_ids, slip_type, channel,
                  message, batch_id=None, message_id=None,
                  delivery_status='sent', message_ids=None):
    """Write one or more rows into dispatch_history with proper tenant_id."""
    tid, _ = repo._resolve_tenant(tenant)
    if not batch_id:
        ph_clean = re.sub(r'\D', '', str(phone or ''))[:15] or 'nophone'
        batch_id = f"b_{int(time.time())}_{ph_clean}_{random.randint(100, 999)}"

    clean_phone = (phone or '').strip() or 'No Phone'
    preview = (message or '')[:120]
    eff_msg_ids = message_ids or message_id

    # Resolve voter rows
    logged_voters = []
    if voter_ids:
        if isinstance(voter_ids, str):
            id_list = [int(x) for x in voter_ids.split(',') if x.strip().isdigit()]
        elif isinstance(voter_ids, list):
            id_list = [int(x) for x in voter_ids if str(x).isdigit()]
        else:
            id_list = []
        if id_list:
            logged_voters = repo.get_voters_by_ids(tenant, id_list)

    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            if not logged_voters:
                cur.execute("""
                    INSERT INTO dispatch_history
                        (tenant_id, batch_id, phone, voter_id, voter_name, voter_name_hi,
                         rel_name, part_no, sl_no, house_no, epic_no, slip_type,
                         channel, message_preview, message_id, delivery_status, message_ids)
                    VALUES (%s, %s, %s, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                            %s, %s, %s, %s, %s, %s)
                """, (tid, batch_id, clean_phone, slip_type, channel,
                      preview, message_id, delivery_status, eff_msg_ids))
            else:
                for v in logged_voters:
                    rel_display = v.get('rel_name') or v.get('rel_name_en') or v.get('rel_name_hi') or ''
                    if v.get('rel_type') and v.get('rel_name_en'):
                        rel_display = f"{v['rel_type']}: {v['rel_name_en']}"
                    cur.execute("""
                        INSERT INTO dispatch_history
                            (tenant_id, batch_id, phone, voter_id, voter_name, voter_name_hi,
                             rel_name, part_no, sl_no, house_no, epic_no, slip_type,
                             channel, message_preview, message_id, delivery_status, message_ids)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        tid, batch_id, clean_phone,
                        v.get('id') or v.get('voter_id'),
                        v.get('name_en'), v.get('name_hi'), rel_display,
                        v.get('part_no'), v.get('sl_no'), v.get('house_no'), v.get('epic_no'),
                        slip_type, channel, preview, message_id, delivery_status, eff_msg_ids
                    ))

            # Auto-update voter contact_phone with dispatched number(s)
            if clean_phone not in ('No Phone', 'nophone') and logged_voters:
                valid_phones = []
                for tok in re.split(r'[,;\s/]+', clean_phone):
                    d = re.sub(r'\D', '', tok)
                    n = _normalize_phone(d)
                    if n and n not in valid_phones:
                        valid_phones.append(n)

                if valid_phones:
                    for v in logged_voters:
                        vid = v.get('id') or v.get('voter_id')
                        if not vid:
                            continue
                        cur.execute("""
                            UPDATE voters SET contact_phone = %s
                            WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                        """, (', '.join(valid_phones), tid, vid, vid))

        conn.commit()


# ---------------------------------------------------------------------------
# GET /api/history
# ---------------------------------------------------------------------------

@history_bp.route('/api/history')
def api_history():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    q = request.args.get('q', '').strip().lower()

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                q_clean = re.sub(r'[^a-z0-9]', '', q)
                q_wild = f"%{q}%"
                q_clean_wild = f"%{q_clean}%" if q_clean else q_wild

                cur.execute("""
                    SELECT * FROM dispatch_history
                    WHERE tenant_id = %s
                      AND (
                            phone ILIKE %s
                         OR voter_name ILIKE %s
                         OR voter_name_hi LIKE %s
                         OR rel_name ILIKE %s
                         OR house_no ILIKE %s
                         OR epic_no ILIKE %s
                         OR channel ILIKE %s
                         OR slip_type ILIKE %s
                         OR REPLACE(LOWER(voter_name), ' ', '') LIKE %s
                      )
                    ORDER BY id DESC LIMIT 5000
                """, (tid, q_wild, q_wild, q_wild, q_wild, q_wild, q_wild,
                      q_wild, q_wild, q_clean_wild))
            else:
                cur.execute("""
                    SELECT * FROM dispatch_history
                    WHERE tenant_id = %s
                    ORDER BY id DESC LIMIT 5000
                """, (tid,))
            rows = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) FROM dispatch_history WHERE tenant_id = %s", (tid,))
            total_dispatches = cur.fetchone()['count']

            cur.execute("""
                SELECT COUNT(DISTINCT phone) FROM dispatch_history
                WHERE tenant_id = %s AND phone NOT IN ('No Phone', 'Printed (No Phone)')
            """, (tid,))
            unique_phones = cur.fetchone()['count']

            cur.execute("""
                SELECT COUNT(DISTINCT COALESCE(voter_id::text, epic_no,
                       (part_no::text || '_' || sl_no::text)))
                FROM dispatch_history
                WHERE tenant_id = %s
                  AND (voter_id IS NOT NULL OR epic_no IS NOT NULL OR sl_no IS NOT NULL)
            """, (tid,))
            unique_voters = cur.fetchone()['count'] or 0

            cur.execute("""
                SELECT DISTINCT voter_id FROM dispatch_history
                WHERE tenant_id = %s AND voter_id IS NOT NULL
            """, (tid,))
            dispatched_voter_ids = [r['voter_id'] for r in cur.fetchall()]

            cur.execute("""
                SELECT voter_id, phone FROM dispatch_history
                WHERE tenant_id = %s AND voter_id IS NOT NULL
                  AND phone IS NOT NULL AND phone != ''
                  AND phone NOT IN ('No Phone', 'Printed (No Phone)')
                ORDER BY id ASC
            """, (tid,))
            dispatched_phones_map = {}
            for row in cur.fetchall():
                vid, ph = row['voter_id'], row['phone']
                if not vid or not ph:
                    continue
                for tok in re.split(r'[,;\s/]+', str(ph)):
                    cph = _normalize_phone(tok)
                    if cph:
                        if vid not in dispatched_phones_map:
                            dispatched_phones_map[vid] = []
                        if cph not in dispatched_phones_map[vid]:
                            dispatched_phones_map[vid].append(cph)

    # Group into batches
    batches_map = {}
    for r in rows:
        bid = r.get('batch_id')
        if not bid:
            time_key = str(r.get('sent_at') or '')[:19]
            bid = f"{r.get('phone')}_{time_key}_{r.get('channel')}_{r.get('slip_type')}"

        row_phone = r.get('phone')
        parsed_phones = []
        if row_phone and row_phone not in ('No Phone', 'Printed (No Phone)'):
            for tok in re.split(r'[,;\s/]+', str(row_phone)):
                cph = _normalize_phone(tok)
                if cph and cph not in parsed_phones:
                    parsed_phones.append(cph)
            if not parsed_phones and row_phone:
                parsed_phones.append(str(row_phone).strip())

        if bid not in batches_map:
            batches_map[bid] = {
                'batch_id': bid,
                'sent_at': str(r.get('sent_at') or ''),
                'phone': row_phone,
                'phones': list(parsed_phones),
                'channel': r.get('channel') or 'WhatsApp',
                'slip_type': r.get('slip_type') or 'Single',
                'message_preview': r.get('message_preview') or '',
                'message_id': r.get('message_id'),
                'delivery_status': r.get('delivery_status') or 'sent',
                'voters': [],
                '_voter_keys': set()
            }
        else:
            if r.get('delivery_status') and r['delivery_status'] != 'sent':
                batches_map[bid]['delivery_status'] = r['delivery_status']
            if r.get('message_id') and not batches_map[bid].get('message_id'):
                batches_map[bid]['message_id'] = r['message_id']
            for p in parsed_phones:
                if p not in batches_map[bid]['phones']:
                    batches_map[bid]['phones'].append(p)
            if batches_map[bid]['phones']:
                batches_map[bid]['phone'] = ', '.join(batches_map[bid]['phones'])

        v_key = (r.get('voter_id'), r.get('part_no'), r.get('sl_no'), r.get('epic_no'))
        if v_key not in batches_map[bid]['_voter_keys'] and (r.get('voter_name') or r.get('voter_id')):
            batches_map[bid]['_voter_keys'].add(v_key)
            batches_map[bid]['voters'].append({
                'id': r.get('voter_id'),
                'name': r.get('voter_name'),
                'name_hi': r.get('voter_name_hi'),
                'rel_name': r.get('rel_name'),
                'part_no': r.get('part_no'),
                'sl_no': r.get('sl_no'),
                'house_no': r.get('house_no'),
                'epic_no': r.get('epic_no'),
                'phone': r.get('phone'),
                'delivery_status': r.get('delivery_status') or 'sent'
            })

    # Merge near-duplicate batches (same voter list sent to multiple phones within 15s)
    merged_batches = []
    seen_merges = set()
    raw_batches = list(batches_map.values())

    for i, b in enumerate(raw_batches):
        if i in seen_merges:
            continue
        v_ids = tuple(sorted([v['id'] for v in b['voters'] if v.get('id')])) or \
                tuple(sorted([f"{v.get('part_no')}_{v.get('sl_no')}" for v in b['voters']]))
        b_time_str = b.get('sent_at', '')[:19]

        for j in range(i + 1, min(i + 20, len(raw_batches))):
            if j in seen_merges:
                continue
            b2 = raw_batches[j]
            v_ids2 = tuple(sorted([v['id'] for v in b2['voters'] if v.get('id')])) or \
                     tuple(sorted([f"{v.get('part_no')}_{v.get('sl_no')}" for v in b2['voters']]))
            b2_time_str = b2.get('sent_at', '')[:19]

            if v_ids and v_ids == v_ids2 and b.get('slip_type') == b2.get('slip_type'):
                time_close = False
                try:
                    t1 = time.mktime(time.strptime(b_time_str, "%Y-%m-%d %H:%M:%S"))
                    t2 = time.mktime(time.strptime(b2_time_str, "%Y-%m-%d %H:%M:%S"))
                    if abs(t1 - t2) <= 15:
                        time_close = True
                except Exception:
                    time_close = (b_time_str == b2_time_str)

                if time_close:
                    seen_merges.add(j)
                    for p in b2.get('phones', []):
                        if p not in b['phones']:
                            b['phones'].append(p)
                    if b['phones']:
                        b['phone'] = ', '.join(b['phones'])

        b.pop('_voter_keys', None)
        merged_batches.append(b)

    for b in merged_batches:
        b['voter_count'] = len(b['voters'])
        parts = sorted(set(v['part_no'] for v in b['voters'] if v.get('part_no')))
        b['parts_display'] = ', '.join(f"P.{p}" for p in parts) if parts else '-'
        houses = sorted(set(str(v['house_no']) for v in b['voters'] if v.get('house_no')))
        b['houses_display'] = (
            ', '.join(houses[:3]) + (f' (+{len(houses)-3})' if len(houses) > 3 else '')
            if houses else '-'
        )

    return jsonify({
        'batches': merged_batches,
        'history': rows,
        'total': unique_voters,
        'total_dispatches': total_dispatches,
        'unique_voters': unique_voters,
        'total_batches': len(merged_batches),
        'unique_phones': unique_phones,
        'dispatched_voter_ids': dispatched_voter_ids,
        'dispatched_phones_map': dispatched_phones_map
    })


# ---------------------------------------------------------------------------
# POST /api/history/log
# ---------------------------------------------------------------------------

@history_bp.route('/api/history/log', methods=['POST'])
@audit_action('dispatch.send', entity_type='dispatch_batch')
def api_history_log():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    data = request.get_json(force=True) or {}
    phone = data.get('phone', '').strip()
    voter = data.get('voter', {})
    voter_ids = data.get('voter_ids', [])
    channel = data.get('channel', 'WhatsApp Web')
    slip_type = data.get('slip_type', 'Single')
    message = data.get('message', '')

    # Update voter contact_phone if valid number provided
    clean_phone = _normalize_phone(phone)
    if clean_phone:
        ids_to_update = []
        if voter_ids:
            if isinstance(voter_ids, list):
                ids_to_update = [int(x) for x in voter_ids if str(x).isdigit()]
            elif isinstance(voter_ids, str):
                ids_to_update = [int(x) for x in voter_ids.split(',') if x.strip().isdigit()]
        elif voter and voter.get('id'):
            ids_to_update = [int(voter['id'])]

        if ids_to_update:
            try:
                with repo.get_connection() as conn:
                    with conn.cursor() as cur:
                        for vid in ids_to_update:
                            cur.execute("""
                                UPDATE voters SET contact_phone = %s
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (clean_phone, tid, vid, vid))
                    conn.commit()
            except Exception as e:
                print(f"Error updating voter contact phone: {e}")

    _log_dispatch(
        repo, tenant,
        phone=phone or 'No Phone',
        voter_ids=voter_ids,
        slip_type=slip_type,
        channel=channel,
        message=message
    )
    return jsonify({'success': True})


# ---------------------------------------------------------------------------
# GET /api/history/export
# ---------------------------------------------------------------------------

@history_bp.route('/api/history/export')
def api_history_export():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    cfg = load_tenant_config(tenant)
    election = cfg.get('election', {})
    slip_place = f"{election.get('ward_name_en', '')} ({election.get('district', '')})"
    ward_label = election.get('ward_name_en', tenant)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM dispatch_history
                WHERE tenant_id = %s ORDER BY id DESC
            """, (tid,))
            rows = [dict(r) for r in cur.fetchall()]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sent History"

    headers = [
        "S.No.", "Date & Time", "Mobile No.", "Voter Name (EN)", "Voter Name (HI)",
        "Relative Name", "Part No.", "Sl. No.", "House No.", "EPIC No.",
        "Slip Type", "Dispatch Channel"
    ]

    ws.append([f"{slip_place} - Voter Slip WhatsApp Dispatch History (मतदाता पर्ची डिस्पैच हिस्ट्री)"])
    ws.append([f"Total Dispatched Records: {len(rows)} voters"])
    ws.append([])
    ws.append(headers)

    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for idx, r in enumerate(rows, 1):
        ws.append([
            idx,
            str(r.get('sent_at') or '')[:19],
            r.get('phone'),
            r.get('voter_name'),
            r.get('voter_name_hi'),
            r.get('rel_name') or '-',
            r.get('part_no'),
            r.get('sl_no'),
            r.get('house_no'),
            r.get('epic_no') or '-',
            r.get('slip_type'),
            r.get('channel')
        ])

    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=10)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    excel_io = io.BytesIO()
    wb.save(excel_io)
    excel_io.seek(0)

    return send_file(
        excel_io,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'{ward_label}_Voter_Slip_Dispatch_History.xlsx'
    )


# ---------------------------------------------------------------------------
# POST /api/history/delete
# ---------------------------------------------------------------------------

@history_bp.route('/api/history/delete', methods=['POST'])
@audit_action('dispatch.delete_record', entity_type='dispatch_record')
def api_history_delete():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    data = request.get_json(silent=True) or {}
    record_id = data.get('id')
    batch_id = data.get('batch_id')
    ids = data.get('ids', [])

    target_ids = []
    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if record_id:
                target_ids.append(int(record_id))
            elif ids:
                target_ids.extend([int(x) for x in ids if str(x).isdigit()])
            elif batch_id:
                cur.execute("""
                    SELECT id FROM dispatch_history
                    WHERE tenant_id = %s AND batch_id = %s
                """, (tid, batch_id))
                target_ids = [r['id'] for r in cur.fetchall()]

            if not target_ids:
                return jsonify({'success': False, 'error': 'No record ID or batch ID specified'}), 400

            cur.execute("""
                SELECT DISTINCT voter_id FROM dispatch_history
                WHERE tenant_id = %s AND id = ANY(%s) AND voter_id IS NOT NULL
            """, (tid, target_ids))
            voter_ids = [r['voter_id'] for r in cur.fetchall() if r['voter_id']]

            cur.execute("""
                DELETE FROM dispatch_history
                WHERE tenant_id = %s AND id = ANY(%s)
            """, (tid, target_ids))
            deleted_count = cur.rowcount

            # Update is_dispatched flag on affected voters
            for vid in voter_ids:
                cur.execute("""
                    SELECT COUNT(*), MAX(sent_at) FROM dispatch_history
                    WHERE tenant_id = %s AND voter_id = %s
                """, (tid, vid))
                row = cur.fetchone()
                cnt = row['count']
                max_sent = row['max']
                if cnt == 0:
                    cur.execute("""
                        UPDATE voters SET is_dispatched = false, dispatched_at = NULL
                        WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                    """, (tid, vid, vid))
                else:
                    cur.execute("""
                        UPDATE voters SET is_dispatched = true, dispatched_at = %s
                        WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                    """, (max_sent, tid, vid, vid))

        conn.commit()

    return jsonify({'success': True, 'deleted_count': deleted_count})


# ---------------------------------------------------------------------------
# POST /api/history/edit
# ---------------------------------------------------------------------------

@history_bp.route('/api/history/edit', methods=['POST'])
@audit_action('history.edit', entity_type='dispatch_record')
def api_history_edit():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    data = request.get_json(silent=True) or {}
    record_id = data.get('id')
    if not record_id:
        return jsonify({'success': False, 'error': 'Record ID is required'}), 400

    phone = data.get('phone')
    notes = data.get('notes')
    delivery_status = data.get('delivery_status')

    updates = []
    params = []
    if phone is not None:
        updates.append("phone = %s")
        params.append(phone.strip())
    if notes is not None:
        updates.append("notes = %s")
        params.append(notes.strip())
    if delivery_status is not None:
        updates.append("delivery_status = %s")
        params.append(delivery_status.strip())

    if not updates:
        return jsonify({'success': False, 'error': 'Nothing to update'}), 400

    params.extend([tid, int(record_id)])
    with repo.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                UPDATE dispatch_history
                SET {', '.join(updates)}
                WHERE tenant_id = %s AND id = %s
            """, params)
        conn.commit()

    return jsonify({'success': True, 'message': 'Record updated successfully'})


# ---------------------------------------------------------------------------
# GET /api/outside_voters
# ---------------------------------------------------------------------------

@history_bp.route('/api/outside_voters')
def api_outside_voters():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    q = request.args.get('q', '').strip().lower()
    part = (request.args.get('part') or request.args.get('part_no') or '').strip()
    ward = (request.args.get('ward') or request.args.get('ward_no') or '').strip()

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Sync voters marked is_outside=true into outside_voters if not already present
            cur.execute("""
                INSERT INTO outside_voters
                    (tenant_id, voter_id, part_no, sl_no, name_en, name_hi,
                     rel_name_en, rel_name_hi, rel_type, house_no, epic_no,
                     age, gender, contact_phone, current_city, notes, marked_at)
                SELECT v.tenant_id, v.voter_id, v.part_no, v.sl_no, v.name_en, v.name_hi,
                       v.rel_name_en, v.rel_name_hi, v.rel_type, v.house_no, v.epic_no,
                       v.age, v.gender, COALESCE(v.contact_phone, ''), '', '', NOW()
                FROM voters v
                WHERE v.tenant_id = %s AND v.is_outside = true
                ON CONFLICT (tenant_id, voter_id) DO UPDATE SET
                    contact_phone = CASE
                        WHEN (outside_voters.contact_phone IS NULL OR outside_voters.contact_phone = '') AND EXCLUDED.contact_phone != ''
                        THEN EXCLUDED.contact_phone
                        ELSE outside_voters.contact_phone
                    END
            """, (tid,))
            conn.commit()

            conditions = ["tenant_id = %s"]
            params = [tid]

            if part and str(part).isdigit():
                conditions.append("part_no = %s")
                params.append(int(part))

            if ward and str(ward).isdigit():
                conditions.append("ward_no = %s")
                params.append(int(ward))

            if q:
                conditions.append("""(
                    name_en ILIKE %s OR name_hi LIKE %s OR
                    rel_name_en ILIKE %s OR rel_name_hi LIKE %s OR
                    contact_phone LIKE %s OR notes ILIKE %s OR
                    house_no ILIKE %s OR epic_no ILIKE %s OR
                    sl_no = %s
                )""")
                q_like = f"%{q}%"
                sl_val = int(q) if q.isdigit() else -1
                params.extend([q_like, q_like, q_like, q_like, q_like, q_like, q_like, q_like, sl_val])

            cur.execute(f"""
                SELECT * FROM outside_voters
                WHERE {' AND '.join(conditions)}
                ORDER BY part_no, house_no, sl_no
            """, params)
            rows = [dict(r) for r in cur.fetchall()]

            # Aggregate totals across ALL outside voters for this tenant
            cur.execute("SELECT * FROM outside_voters WHERE tenant_id = %s", (tid,))
            all_outside = [dict(r) for r in cur.fetchall()]

    # Group filtered results into families by (part_no, house_no)
    families_map = {}
    for r in rows:
        h = str(r.get('house_no') or '').strip()
        if h and h != '0':
            key = f"{r['part_no']}_{h}"
        elif r.get('contact_phone'):
            key = f"{r['part_no']}_{h}_{r['contact_phone']}"
        else:
            key = f"{r['part_no']}_{h}_v{r['voter_id']}"

        if key not in families_map:
            families_map[key] = {
                'family_key': key,
                'part_no': r['part_no'],
                'house_no': r['house_no'],
                'contact_phone': r.get('contact_phone') or '',
                'notes': r.get('notes') or '',
                'marked_at': str(r.get('marked_at') or ''),
                'members': []
            }
        if not families_map[key]['contact_phone'] and r.get('contact_phone'):
            families_map[key]['contact_phone'] = r['contact_phone']
        families_map[key]['members'].append(r)

    all_fam_map = {}
    for x in all_outside:
        xh = str(x.get('house_no') or '').strip()
        xk = (f"{x['part_no']}_{xh}" if (xh and xh != '0')
              else f"{x['part_no']}_{xh}_{x.get('contact_phone') or ''}_v{x['voter_id']}")
        if xk not in all_fam_map:
            all_fam_map[xk] = x.get('contact_phone') or ''

    return jsonify({
        'outside_voters': rows,
        'families': list(families_map.values()),
        'total_voters': len(all_outside),
        'total_families': len(all_fam_map),
        'families_with_phone': sum(1 for p in all_fam_map.values() if p),
        'total': len(all_outside),
        'with_phone': sum(1 for p in all_fam_map.values() if p),
        'shown': len(rows)
    })


# ---------------------------------------------------------------------------
# POST /api/outside_voters/mark
# ---------------------------------------------------------------------------

@history_bp.route('/api/outside_voters/mark', methods=['POST'])
@audit_action('voter.mark_outside', entity_type='voter')
def api_outside_voters_mark():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    try:
        data = request.get_json(force=True) or {}
        voter_ids = data.get('voter_ids', [])
        unmark_ids = data.get('unmark_ids', [])
        phone = str(data.get('contact_phone', '') or '').strip()
        notes = str(data.get('notes', '') or '').strip()

        if not voter_ids:
            voter_id = data.get('voter_id')
            if voter_id:
                voter_ids = [voter_id]

        voter_ids = [int(x) for x in voter_ids if str(x).isdigit()]
        unmark_ids = [int(x) for x in unmark_ids if str(x).isdigit()]

        if not voter_ids and not unmark_ids:
            return jsonify({'success': False, 'error': 'No voters specified'}), 400

        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if unmark_ids:
                    cur.execute("""
                        DELETE FROM outside_voters
                        WHERE tenant_id = %s AND voter_id = ANY(%s)
                    """, (tid, unmark_ids))
                    cur.execute("""
                        UPDATE voters SET is_outside = false
                        WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                    """, (tid, unmark_ids, unmark_ids))

                voters = []
                if voter_ids:
                    cur.execute("""
                        SELECT * FROM voters
                        WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                    """, (tid, voter_ids, voter_ids))
                    voters = [dict(r) for r in cur.fetchall()]

                    for v in voters:
                        vid = v.get('voter_id') or v.get('id') or v.get('legacy_id')
                        target_phone = phone if phone else (v.get('contact_phone') or '')
                        cur.execute("""
                            INSERT INTO outside_voters
                                (tenant_id, voter_id, part_no, sl_no, name_en, name_hi,
                                 rel_name_en, rel_name_hi, rel_type, house_no, epic_no,
                                 age, gender, contact_phone, current_city, notes, marked_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '', %s, NOW())
                            ON CONFLICT (tenant_id, voter_id) DO UPDATE SET
                                contact_phone = CASE
                                    WHEN EXCLUDED.contact_phone != '' THEN EXCLUDED.contact_phone
                                    ELSE outside_voters.contact_phone
                                END,
                                notes = EXCLUDED.notes,
                                marked_at = NOW()
                        """, (
                            tid, vid, v.get('part_no'), v.get('sl_no'),
                            v.get('name_en'), v.get('name_hi'),
                            v.get('rel_name_en'), v.get('rel_name_hi'), v.get('rel_type'),
                            v.get('house_no'), v.get('epic_no'),
                            v.get('age'), v.get('gender'),
                            target_phone, notes
                        ))
                        if phone:
                            cur.execute("""
                                UPDATE voters SET is_outside = true, contact_phone = %s
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (phone, tid, vid, vid))
                        else:
                            cur.execute("""
                                UPDATE voters SET is_outside = true
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (tid, vid, vid))

            conn.commit()

        return jsonify({'success': True, 'count': len(voters)})
    except Exception as e:
        print(f"Error marking outside voter: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# POST /api/outside_voters/unmark
# ---------------------------------------------------------------------------

@history_bp.route('/api/outside_voters/unmark', methods=['POST'])
@audit_action('voter.unmark_outside', entity_type='voter')
def api_outside_voters_unmark():
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    try:
        data = request.get_json(force=True) or {}
        voter_ids = data.get('voter_ids', [])
        voter_id = data.get('voter_id')
        if voter_id and voter_id not in voter_ids:
            voter_ids.append(voter_id)

        voter_ids = [int(x) for x in voter_ids if str(x).isdigit()]
        if not voter_ids:
            return jsonify({'success': False, 'error': 'No voter_id provided'}), 400

        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM outside_voters
                    WHERE tenant_id = %s AND voter_id = ANY(%s)
                """, (tid, voter_ids))
                cur.execute("""
                    UPDATE voters SET is_outside = false
                    WHERE tenant_id = %s AND (voter_id = ANY(%s) OR legacy_id = ANY(%s))
                """, (tid, voter_ids, voter_ids))
            conn.commit()

        return jsonify({'success': True, 'count': len(voter_ids)})
    except Exception as e:
        print(f"Error unmarking outside voter: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /api/outside_voters/export
# ---------------------------------------------------------------------------

@history_bp.route('/api/outside_voters/export')
def api_outside_voters_export():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    cfg = load_tenant_config(tenant)
    election = cfg.get('election', {})
    slip_place = f"{election.get('ward_name_en', '')} ({election.get('district', '')})"
    ward_label = election.get('ward_name_en', tenant)

    with repo.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Sync voters marked is_outside=true into outside_voters if not already present
            cur.execute("""
                INSERT INTO outside_voters
                    (tenant_id, voter_id, part_no, sl_no, name_en, name_hi,
                     rel_name_en, rel_name_hi, rel_type, house_no, epic_no,
                     age, gender, contact_phone, current_city, notes, marked_at)
                SELECT v.tenant_id, v.voter_id, v.part_no, v.sl_no, v.name_en, v.name_hi,
                       v.rel_name_en, v.rel_name_hi, v.rel_type, v.house_no, v.epic_no,
                       v.age, v.gender, COALESCE(v.contact_phone, ''), '', '', NOW()
                FROM voters v
                WHERE v.tenant_id = %s AND v.is_outside = true
                ON CONFLICT (tenant_id, voter_id) DO UPDATE SET
                    contact_phone = CASE
                        WHEN (outside_voters.contact_phone IS NULL OR outside_voters.contact_phone = '') AND EXCLUDED.contact_phone != ''
                        THEN EXCLUDED.contact_phone
                        ELSE outside_voters.contact_phone
                    END
            """, (tid,))
            conn.commit()

            cur.execute("""
                SELECT * FROM outside_voters
                WHERE tenant_id = %s ORDER BY part_no, house_no, sl_no
            """, (tid,))
            rows = [dict(r) for r in cur.fetchall()]

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Outside Voters"

    headers = [
        "S.No.", "Part No.", "Sl. No.", "Voter Name (EN)", "Voter Name (HI)",
        "Relative Name", "House No.", "Age / Gender", "EPIC No.",
        "Family Calling Phone", "Calling Notes", "Marked Date"
    ]

    ws.append([f"{slip_place} - Outside / Non-Resident Voters Calling List (प्रवासी परिवार सूची)"])
    ws.append([f"Total Identified Outside Voters: {len(rows)} voters"])
    ws.append([])
    ws.append(headers)

    header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid")
    header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=4, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for idx, r in enumerate(rows, 1):
        rel = (f"{r.get('rel_type') or 'Rel'}: {r.get('rel_name_en') or ''}"
               + (f" ({r['rel_name_hi']})" if r.get('rel_name_hi') else ''))
        age_gender = f"{r.get('age') or ''} Yrs / {r.get('gender') or ''}"
        ws.append([
            idx, r.get('part_no'), r.get('sl_no'),
            r.get('name_en'), r.get('name_hi'), rel,
            r.get('house_no'), age_gender, r.get('epic_no'),
            r.get('contact_phone') or 'Not Given',
            r.get('notes') or '-',
            str(r.get('marked_at') or '')[:16]
        ])

    for col in ws.columns:
        max_len = max((len(str(cell.value or '')) for cell in col), default=10)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    excel_io = io.BytesIO()
    wb.save(excel_io)
    excel_io.seek(0)

    return send_file(
        excel_io,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'{ward_label}_Outside_Voters_Calling_List.xlsx'
    )


# ---------------------------------------------------------------------------
# POST /api/voter/update_phone
# ---------------------------------------------------------------------------

@history_bp.route('/api/voter/update_phone', methods=['POST'])
@audit_action('voter.update_phone', entity_type='voter')
def api_voter_update_phone():
    if session.get('role') == 'viewer':
        return jsonify({'success': False, 'error': 'दर्शक (Viewer) केवल देखने के लिए अधिकृत है। डेटा संपादित करने की अनुमति नहीं है (Viewer role is read-only)'}), 403
    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    try:
        data = request.get_json(force=True) or {}
        voter_id = data.get('voter_id')
        raw_phone = str(data.get('phone', '') or '').strip()

        if not voter_id:
            return jsonify({'success': False, 'error': 'voter_id is required'}), 400
        try:
            voter_id = int(voter_id)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid voter_id'}), 400

        clean_phone = ''
        if raw_phone:
            clean_phone = _normalize_phone(raw_phone)
            if not clean_phone and raw_phone:
                return jsonify({'success': False,
                                'error': 'कृपया 10-अंकों का वैध मोबाइल नंबर दर्ज करें (शुरुआत 6, 7, 8 या 9 से)'}), 400

        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT voter_id AS id, part_no, house_no, name_en, name_hi
                    FROM voters
                    WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                    LIMIT 1
                """, (tid, voter_id, voter_id))
                target_voter = cur.fetchone()
                if not target_voter:
                    return jsonify({'success': False, 'error': 'Voter not found'}), 404

                part_no = target_voter['part_no']
                house_no = str(target_voter['house_no'] or '').strip()

                # Strictly update ONLY the target voter, never other family or household members
                target_voter_ids = [voter_id]

                cur.execute("""
                    UPDATE voters SET contact_phone = %s
                    WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                """, (clean_phone, tid, voter_id, voter_id))

                try:
                    cur.execute("""
                        UPDATE outside_voters SET contact_phone = %s
                        WHERE tenant_id = %s AND voter_id = %s
                    """, (clean_phone, tid, voter_id))
                except Exception:
                    pass

            conn.commit()

        return jsonify({
            'success': True,
            'phone': clean_phone,
            'voter_id': voter_id,
            'updated_ids': target_voter_ids,
            'updated_count': 1,
            'part_no': part_no,
            'house_no': house_no
        })
    except Exception as e:
        print(f"Error updating voter phone: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
