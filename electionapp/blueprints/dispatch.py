"""
dispatch.py
Dispatch Blueprint for Unified Multi-Tenant Voter Platform:
- Automated WhatsApp slip dispatch (/api/dispatch/send_slip)
- 1-Click WhatsApp deep link generator (/api/dispatch/wa_me_link)
- Tenant dispatch history log (/api/dispatch/history)
- Official Meta WhatsApp dispatch engine (/api/send_openclaw)
- Poster card image generator & server (/api/slip_card_image)
- Meta WhatsApp Webhook handler (/api/webhook/meta)
"""

import os
import re
import time
import random
import psycopg2.extras
from flask import Blueprint, jsonify, request, current_app, g, send_file, session, send_from_directory

import meta_whatsapp
from electionapp import whatsapp_quota
from electionapp.audit import audit_action
from electionapp.dispatch.wa_me import WaMeChannel
from electionapp.config import load_tenant_config
from electionapp.blueprints.slip import is_struck_off, STRUCK_OFF_ERROR
from electionapp.tenancy import resolve_tenant
from electionapp.slip_prewarm import get_prewarmed_slip, trigger_background_prewarm, resolve_slip_type

dispatch_bp = Blueprint('dispatch', __name__)


def _openclaw_message(sent_count, quota_skipped):
    """Bilingual result line; names the quota explicitly when a batch was truncated."""
    base = f'{sent_count} मोबाइल नंबर पर पर्ची भेज दी गई है (डिलीवरी प्रक्रियाधीन है ✓)...'
    if quota_skipped:
        base += (f' ⚠️ WhatsApp संदेश सीमा समाप्त — {len(quota_skipped)} नंबर छोड़ दिये गये। '
                 f'(Message limit reached, {len(quota_skipped)} skipped.)')
    return base


def _get_tenant():
    """Resolve active tenant slug and repo from authoritative tenancy resolver."""
    return resolve_tenant()


@dispatch_bp.route('/api/dispatch/send_slip', methods=['POST'])
@audit_action('dispatch.send', entity_type='dispatch_batch')
def api_dispatch_send_slip():
    if session.get('role') == 'karyakarta':
        return jsonify({'success': False, 'error': 'कार्यकर्ता को व्हाट्सएप भेजने की अनुमति नहीं है (Restricted: Karyakartas cannot send WhatsApp)'}), 403

    """
    Automated WhatsApp slip dispatch.
    Triggers official Meta WhatsApp Cloud API via DispatchRouter with consent gate check.
    """
    router = current_app.extensions.get('router')
    tenant, repo = resolve_tenant()
    campaign = current_app.config.get('CAMPAIGN', {})

    data = request.get_json(silent=True) or request.form or {}
    phone = data.get('phone', '').strip()
    message = data.get('message') or campaign.get('campaign_message', 'मतदाता सूचना पर्ची')
    image_path = data.get('image_path', '')
    voter_id = data.get('voter_id')

    voter_data = None
    if voter_id and repo:
        try:
            voter_data = repo.get_voter(tenant, int(voter_id))
            if not phone and voter_data:
                phone = voter_data.get('contact_phone', '').strip()
        except Exception:
            pass

    if is_struck_off(voter_data):
        return jsonify({'success': False, 'error': STRUCK_OFF_ERROR}), 422

    if not phone:
        return jsonify({'success': False, 'error': 'Recipient phone number is required'}), 400

    if not router:
        return jsonify({'success': False, 'error': 'Dispatch router extension not configured'}), 500

    # The router reserves and releases the WhatsApp credit itself (see
    # DispatchRouter._reserve_credit), so there is nothing to gate here.
    result = router.dispatch_slip(
        phone=phone,
        message=message,
        image_path=image_path,
        voter_data=voter_data,
        override_channel_order=['meta_cloud']
    )

    if result.success:
        status_code = 200
    elif result.channel == 'quota_gate':
        status_code = 429
    else:
        status_code = 400
    return jsonify(result.to_dict()), status_code


@dispatch_bp.route('/api/dispatch/wa_me_link', methods=['GET', 'POST'])
def api_dispatch_wa_me_link():
    """
    Generates volunteer WhatsApp Web / mobile 1-click sharing deep link (https://wa.me/...).
    """
    data = request.get_json(silent=True) if request.is_json else request.args
    phone = data.get('phone', '').strip()
    message = data.get('message', '')

    tenant, _ = resolve_tenant()
    try:
        t_cfg = load_tenant_config(tenant)
    except Exception:
        t_cfg = current_app.config.get('CAMPAIGN', {})

    if not message:
        message = t_cfg.get('campaign_message', 'सादर प्रणाम! मतदान अवश्य करें।')

    wa_channel = WaMeChannel()
    link = wa_channel.generate_link(phone, message)

    return jsonify({
        'success': True,
        'phone': phone,
        'link': link,
        'tenant': tenant
    }), 200


@dispatch_bp.route('/api/dispatch/history')
def api_dispatch_history():
    """
    Retrieves audit history of all dispatched messages from the repository.
    """
    repo = current_app.extensions.get('repo')
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    tenant = request.args.get('tenant') or getattr(g, 'tenant_slug', None) or request.cookies.get('voter_tenant') or getattr(repo, 'tenant_slug', '') or ''
    tenant = str(tenant).strip().lower()
    voter_param = request.args.get('voter_id')
    voter_id = int(voter_param) if voter_param and str(voter_param).isdigit() else None
    limit = request.args.get('limit', default=50, type=int)

    history = repo.get_dispatch_history(tenant, voter_id=voter_id, limit=limit)
    return jsonify({
        'success': True,
        'tenant': tenant,
        'voter_id': voter_id,
        'total': len(history),
        'history': history
    }), 200


# ---------------------------------------------------------------------------
# META WHATSAPP DISPATCH ENGINE (/api/send_openclaw)
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/send_openclaw', methods=['POST'])
@audit_action('dispatch.send_bulk', entity_type='dispatch_batch')
def api_send_openclaw():
    if session.get('role') == 'karyakarta':
        return jsonify({'success': False, 'error': 'कार्यकर्ता को व्हाट्सएप भेजने की अनुमति नहीं है (Restricted: Karyakartas cannot send WhatsApp)'}), 403

    tenant, repo = _get_tenant()
    tid, _ = repo._resolve_tenant(tenant)
    cfg = load_tenant_config(tenant)

    try:
        from electionapp.card_generator import generate_all_bjp_voter_card_pages
    except ImportError:
        return jsonify({'success': False, 'error': 'Card generation module not available'}), 500

    credit = None

    try:
        data = request.get_json(force=True) or {}
        phone = data.get('phone', '')
        phones = data.get('phones', [])
        voter = data.get('voter', {})
        voter_ids = data.get('voter_ids', [])
        slip_type = data.get('slip_type')

        # 1. Parse and extract all unique 10-digit recipient phone numbers
        recipient_phones = []
        if isinstance(phones, list):
            for p in phones:
                clean = re.sub(r'\D', '', str(p))
                if clean.startswith('91') and len(clean) == 12: clean = clean[2:]
                if clean.startswith('0') and len(clean) == 11: clean = clean[1:]
                if len(clean) == 10 and clean not in recipient_phones:
                    recipient_phones.append(clean)
        if phone:
            raw_tokens = re.split(r'[,;\s\n\/]+', str(phone))
            for t in raw_tokens:
                clean = re.sub(r'\D', '', t)
                if clean.startswith('91') and len(clean) == 12: clean = clean[2:]
                if clean.startswith('0') and len(clean) == 11: clean = clean[1:]
                if len(clean) == 10 and clean not in recipient_phones:
                    recipient_phones.append(clean)

        if not recipient_phones:
            return jsonify({'success': False, 'error': 'कृपया वैध 10-अंकों का मोबाइल नंबर दर्ज करें (Please enter a valid 10-digit mobile number)'}), 400

        # 2. Fetch voter records to generate personalized BJP Card Image
        target_voters = []
        if voter_ids:
            id_list = [int(x) for x in voter_ids if str(x).isdigit()]
            if id_list:
                target_voters = repo.get_voters_by_ids(tenant, id_list)
        elif voter and voter.get('id'):
            v = repo.get_voter(tenant, int(voter['id']))
            target_voters = [v] if v else [voter]
        elif voter:
            target_voters = [voter]

        # Determine household consistency and slip_type
        v_primary = target_voters[0] if target_voters else (voter or {})
        slip_type = resolve_slip_type(target_voters, slip_type)
        if len(target_voters) > 1:
            if slip_type == 'Family':
                p_name = f"{v_primary.get('name_hi') or v_primary.get('name_en') or 'मतदाता'} व परिवार ({len(target_voters)} सदस्य)"
            else:
                p_name = f"सम्मानित मतदातागण ({len(target_voters)} सदस्य)"
        else:
            p_name = v_primary.get('name_hi') or v_primary.get('name_en') or 'सम्मानित मतदाता'

        # 3. Check prewarm cache first, or generate all card pages
        pw_ids = [int(v['id']) for v in target_voters if v and v.get('id')]
        prewarmed_paths, prewarmed_media = get_prewarmed_slip(tenant, slip_type, pw_ids)
        if prewarmed_paths:
            card_img_paths = prewarmed_paths
            print(f"[PREWARM_HIT] Using prewarmed slip for {slip_type} ({len(card_img_paths)} pages)", flush=True)
        else:
            prewarmed_media = None
            card_img_paths = generate_all_bjp_voter_card_pages(target_voters, slip_type=slip_type, page_size=8, tenant=tenant)

        # 4. Dispatch to each recipient phone number
        success_phones = []
        failed_phones = []
        last_resp_data = {}

        default_campaign_msg = cfg.get('campaign_message', 'सादर प्रणाम! मतदान अवश्य करें।')
        cand_name_raw = (cfg.get('candidate', {}).get('name_hi') or cfg.get('candidate', {}).get('name_en') or '').strip()
        gp_name_raw = (cfg.get('election', {}).get('ward_name_hi') or '').replace('ग्राम पंचायत ', '').strip()
        ward_no_raw = str(v_primary.get('ward_no') or cfg.get('election', {}).get('ward_no') or '').strip()
        party_name_raw = (cfg.get('party', {}).get('name_hi') or cfg.get('party', {}).get('name_en') or '').strip()

        default_campaign_msg = default_campaign_msg.replace('{voter_name}', p_name)
        default_campaign_msg = default_campaign_msg.replace('{gp_name}', gp_name_raw)
        default_campaign_msg = default_campaign_msg.replace('{candidate_name}', cand_name_raw)
        default_campaign_msg = default_campaign_msg.replace('{ward_no}', ward_no_raw)
        default_campaign_msg = default_campaign_msg.replace('{party_name}', party_name_raw)
        default_campaign_msg = re.sub(r'\{[^\}]*\}', '', default_campaign_msg).strip()
        caption_msg = "नमस्कार,\n\n" + default_campaign_msg

        p_part = v_primary.get('part_no', 1)
        p_ward = v_primary.get('ward_no')
        p_sl = ', '.join(str(v.get('sl_no')) for v in target_voters[:3]) if target_voters else str(v_primary.get('sl_no', ''))
        booths_dict = repo.get_booths_dict(tenant)
        b_info = booths_dict.get(str(p_part)) or booths_dict.get(p_part, {})
        p_booth = b_info.get('name') or v_primary.get('booth_name') or 'राजकीय मतदान केंद्र'

        shared_batch_id = f"b_{int(time.time())}_{random.randint(1000, 9999)}"
        last_used_channel = None
        all_page_results = []

        cand_name = (cfg.get('candidate', {}).get('name_hi') or '').strip()
        cand_party = (cfg.get('party', {}).get('name_hi') or '').strip()
        election_type = str(cfg.get('election', {}).get('election_type') or 'MUNICIPAL').strip().upper()
        if election_type == 'PANCHAYAT':
            cand_title = "सरपंच प्रत्याशी"
        elif election_type == 'PANCHAYAT_SAMITI':
            cand_title = f"{cand_party} प्रत्याशी" if cand_party else "पंचायत समिति सदस्य प्रत्याशी"
        elif election_type == 'ZILA_PARISHAD':
            cand_title = f"{cand_party} प्रत्याशी" if cand_party else "जिला परिषद सदस्य प्रत्याशी"
        else:
            cand_title = f"{cand_party} प्रत्याशी" if cand_party else "प्रत्याशी"

        if cand_name and cand_title:
            candidate_display = f"{cand_name} ({cand_title})"
        elif cand_name:
            candidate_display = cand_name
        elif cand_party:
            candidate_display = cand_party
        else:
            ward_title = cfg.get('election', {}).get('ward_name_hi') or cfg.get('election', {}).get('app_title') or 'चुनाव प्रबंधन समिति'
            candidate_display = f"{ward_title} (मतदाता सेवा)"

        camp_slogan = (cfg.get('party', {}).get('slogan') or cfg.get('slogan') or '').strip()

        # WhatsApp quota gate. Reserve the ceiling for this batch (one credit per
        # recipient per card page) BEFORE any API call. A partial grant means we send
        # up to the limit and then stop; unspent and failed credits are handed back
        # when the reservation closes in the `finally` below.
        max_sends = len(recipient_phones) * max(1, len(card_img_paths))
        credit = whatsapp_quota.reserve(tid, max_sends)

        quota_skipped = []
        for p_clean in recipient_phones:
            if credit.remaining <= 0:
                # Credits ran out mid-batch: skip the rest and report them.
                quota_skipped.append(p_clean)
                continue

            phone_success = False
            phone_error = None
            used_channel = None
            page_results = []

            tot_pages = len(card_img_paths)
            for img_idx, img_path in enumerate(card_img_paths):
                if credit.remaining <= 0:
                    page_results.append({'page': img_idx + 1, 'status': 'skipped',
                                         'error': 'WhatsApp quota exhausted'})
                    continue
                curr_page = img_idx + 1
                page_voter_name = f"{p_name} (पेज {curr_page}/{tot_pages})" if tot_pages > 1 else p_name

                if img_idx > 0:
                    time.sleep(1.5)

                page_media_ids = prewarmed_media.get(img_path) if prewarmed_media else None
                meta_ok, meta_res, meta_err = meta_whatsapp.send_voter_slip_card(
                    phone=p_clean,
                    voter_name=page_voter_name,
                    part_no=p_part,
                    sl_no=p_sl,
                    booth_name=p_booth,
                    image_path=img_path,
                    candidate_name=candidate_display,
                    campaign_message=default_campaign_msg,
                    ward_no=p_ward,
                    slogan=camp_slogan,
                    tenant_slug=tenant,
                    campaign_cfg=cfg,
                    credit=credit,
                    prewarmed_media_ids=page_media_ids
                )

                # One blind retry. A failed attempt refunds its credit, so the retry
                # claims a fresh one; it is skipped when the balance is spent.
                if not meta_ok and credit.remaining > 0:
                    time.sleep(2.0)
                    meta_ok, meta_res, meta_err = meta_whatsapp.send_voter_slip_card(
                        phone=p_clean,
                        voter_name=page_voter_name,
                        part_no=p_part,
                        sl_no=p_sl,
                        booth_name=p_booth,
                        image_path=img_path,
                        candidate_name=candidate_display,
                        campaign_message=default_campaign_msg,
                        ward_no=p_ward,
                        slogan=camp_slogan,
                        tenant_slug=tenant,
                        campaign_cfg=cfg,
                        credit=credit
                    )


                if meta_ok:
                    phone_success = True
                    used_channel = 'GreenTick / AOC' if (isinstance(meta_res, dict) and meta_res.get('provider') in ('aoc_greentick', 'aoc')) else (meta_res.get('provider') if isinstance(meta_res, dict) and meta_res.get('provider') else 'Meta Cloud API')
                    last_resp_data = meta_res
                    page_results.append({'page': curr_page, 'status': 'sent', 'channel': 'meta', 'message_id': meta_res.get('message_id')})
                else:
                    page_results.append({'page': curr_page, 'status': 'failed', 'error': meta_err})
                    phone_error = meta_err or (meta_res.get('error') if isinstance(meta_res, dict) else 'Failed')

            all_page_results.extend(page_results)
            all_pages_ok = all(pr.get('status') == 'sent' for pr in page_results) if page_results else False
            if all_pages_ok or phone_success:
                success_phones.append(p_clean)
                last_used_channel = used_channel or last_used_channel
            else:
                failed_phones.append({'phone': p_clean, 'error': phone_error or 'Failed', 'page_results': page_results})

        if success_phones:
            sent_msg_ids = [pr.get('message_id') for pr in all_page_results if pr.get('message_id')]
            primary_msg_id = sent_msg_ids[0] if sent_msg_ids else (last_resp_data.get('message_id') if last_resp_data else None)
            all_msg_ids_str = ",".join(sent_msg_ids) if sent_msg_ids else primary_msg_id

            # Identify primary recipient voter (Mukhiya/head of family or first voter)
            primary_vid = None
            for v in target_voters:
                if v.get('is_family_head') or v.get('is_mukhiya'):
                    primary_vid = v.get('id') or v.get('voter_id')
                    break
            if not primary_vid and target_voters:
                primary_vid = target_voters[0].get('id') or target_voters[0].get('voter_id')

            # Log to dispatch_history
            with repo.get_connection() as conn:
                with conn.cursor() as cur:
                    for v in target_voters:
                        vid = v.get('id') or v.get('voter_id')
                        rel_display = v.get('rel_name_en') or v.get('rel_name_hi') or ''
                        if v.get('rel_type') and v.get('rel_name_en'):
                            rel_display = f"{v['rel_type']}: {v['rel_name_en']}"
                        cur.execute("""
                            INSERT INTO dispatch_history
                                (tenant_id, batch_id, phone, voter_id, voter_name, voter_name_hi,
                                 rel_name, part_no, sl_no, house_no, epic_no, slip_type,
                                 channel, message_preview, message_id, delivery_status, message_ids)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            tid, shared_batch_id, ", ".join(success_phones), vid,
                            v.get('name_en'), v.get('name_hi'), rel_display,
                            v.get('part_no'), v.get('sl_no'), v.get('house_no'), v.get('epic_no'),
                            slip_type, last_used_channel or 'Meta Cloud API',
                            caption_msg[:120], primary_msg_id, 'sent', all_msg_ids_str
                        ))
                        # Mark voter as dispatched; store phone ONLY on primary/mukhiya voter so family members' records are not polluted
                        if vid == primary_vid and success_phones:
                            cur.execute("""
                                UPDATE voters
                                SET is_dispatched = true, dispatched_at = NOW(), contact_phone = %s
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (", ".join(success_phones), tid, vid, vid))
                        else:
                            cur.execute("""
                                UPDATE voters
                                SET is_dispatched = true, dispatched_at = NOW()
                                WHERE tenant_id = %s AND (voter_id = %s OR legacy_id = %s)
                            """, (tid, vid, vid))
                conn.commit()

            return jsonify({
                'success': True,
                'delivered': False,
                'status': 'sent',
                'count': len(success_phones),
                'sent_to': success_phones,
                'failed': failed_phones,
                'posters_per_person': len(card_img_paths),
                'quota_skipped': quota_skipped,
                'quota_remaining': credit.remaining,
                'message': _openclaw_message(len(success_phones), quota_skipped)
            }), 200
        else:
            first_err = failed_phones[0]['error'] if failed_phones else 'Could not send WhatsApp message'
            not_on_wa = (last_resp_data or {}).get('not_on_whatsapp', False)
            return jsonify({'success': False, 'error': first_err, 'not_on_whatsapp': not_on_wa,
                            'failed': failed_phones, 'quota_skipped': quota_skipped,
                            'quota_remaining': credit.remaining}), 400

    except whatsapp_quota.QuotaError as qe:
        # Quota refused this dispatch (exhausted, blocked, or unreadable). Fail CLOSED.
        return jsonify({
            'success': False,
            'error': str(qe),
            'quota_blocked': True,
        }), 429
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        # Always hand back unspent credits, including on an early return or exception.
        if credit is not None:
            credit.close()


# ---------------------------------------------------------------------------
# SLIP CARD IMAGE ENDPOINT (/api/slip_card_image)
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/slip_card_image')
def api_slip_card_image():
    tenant, repo = _get_tenant()
    voter_id = request.args.get('id', type=int) or request.args.get('voter_id', type=int)
    family_ids = request.args.get('family_ids', '') or request.args.get('voter_ids', '')
    slip_type = re.sub(r'[^A-Za-z0-9_]', '', request.args.get('type', 'Single')) or 'Single'
    page = max(1, request.args.get('page', 1, type=int) or 1)
    page_size = max(1, min(request.args.get('page_size', 8, type=int) or 8, 50))

    target_voters = []
    if family_ids:
        id_list = [int(x) for x in family_ids.split(',') if x.strip().isdigit()]
        if id_list:
            target_voters = repo.get_voters_by_ids(tenant, id_list)
    elif voter_id:
        v = repo.get_voter(tenant, voter_id)
        if v:
            target_voters = [v]

    if not target_voters:
        return "Voter not found", 404

    # A struck-off elector must never be handed a printable slip.
    target_voters = [v for v in target_voters if not is_struck_off(v)]
    if not target_voters:
        return STRUCK_OFF_ERROR, 422

    try:
        from electionapp.card_generator import generate_bjp_voter_card
        card_path = generate_bjp_voter_card(target_voters, slip_type=slip_type, page=page, page_size=page_size, tenant=tenant)
        if card_path and os.path.exists(card_path):
            mime = ('image/jpeg' if card_path.lower().endswith(('.jpg', '.jpeg'))
                    else 'image/png')
            return send_file(card_path, mimetype=mime)
        return "Could not generate card", 500
    except Exception as e:
        return f"Error generating card: {e}", 500


# ---------------------------------------------------------------------------
# WHATSAPP & META DELIVERY STATUS WEBHOOKS (/api/webhook/whatsapp, /api/webhook/meta)
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/webhook/whatsapp', methods=['GET', 'POST'])
@dispatch_bp.route('/api/webhook/meta', methods=['GET', 'POST'])
def api_webhook_whatsapp():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge') or request.args.get('challenge')
        verify_token = os.environ.get('META_WA_WEBHOOK_VERIFY_TOKEN', 'jelatra_chunav_saathi_meta_webhook_2026')
        if (mode == 'subscribe' and token == verify_token) or challenge:
            return challenge or 'ok', 200
        return 'ok', 200

    try:
        payload = request.get_json(force=True, silent=True) or {}
        print(f"[WA_WEBHOOK_RAW] {json.dumps(payload, ensure_ascii=False)}", flush=True)
        repo = current_app.extensions.get('repo')
        if not repo:
            return jsonify({'status': 'ok'}), 200

        # Extract all status update items (supports GreenTick, Meta Cloud API, array payloads)
        items = []
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            if 'entry' in payload and isinstance(payload['entry'], list):
                # Meta Cloud API standard format
                for entry in payload.get('entry', []):
                    for change in entry.get('changes', []):
                        val = change.get('value', {})
                        for st in val.get('statuses', []):
                            items.append({
                                'messageId': st.get('id'),
                                'status': st.get('status'),
                                'recipient': st.get('recipient_id'),
                                'channel': 'Meta Cloud API',
                                'raw_event': st
                            })
            elif 'data' in payload and isinstance(payload['data'], list):
                for d in payload['data']:
                    if isinstance(d, dict):
                        items.append({**d, 'event': payload.get('event')})
                    else:
                        items.append(d)
            elif 'data' in payload and isinstance(payload['data'], dict):
                items.append({**payload['data'], 'event': payload.get('event')})
            else:
                items = [payload]

        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    raw_id = item.get('messageId') or item.get('id') or item.get('wamid') or item.get('msgId')
                    raw_status = str(item.get('status') or item.get('event') or '').lower().strip()
                    if not raw_id or not raw_status:
                        continue

                    # Canonical normalization of delivery status
                    if 'read' in raw_status:
                        status_str = 'read'
                    elif 'delivered' in raw_status:
                        status_str = 'delivered'
                    elif any(w in raw_status for w in ('fail', 'reject', 'undeliver', 'error', 'drop')):
                        status_str = 'failed'
                    elif 'sent' in raw_status:
                        status_str = 'sent'
                    else:
                        status_str = raw_status

                    # Clean messageId suffix (e.g. ee7d9a88-9bfe-43ea...:1 -> ee7d9a88-9bfe-43ea...)
                    clean_id = str(raw_id).split(':')[0].strip()
                    like_pat = f"%{clean_id}%"
                    ch_hint = item.get('channel') or 'GreenTick / AOC'
                    ch_name = f"{ch_hint} ({status_str.title()})"

                    # Fast indexed lookup first (uses idx_dispatch_history_message_id)
                    cur.execute("""
                        UPDATE dispatch_history
                        SET delivery_status = CASE
                                WHEN delivery_status = 'read' AND %s = 'delivered' THEN 'read'
                                ELSE %s
                            END,
                            channel = COALESCE(channel, %s),
                            message_id = COALESCE(message_id, %s)
                        WHERE message_id = %s OR message_id = %s
                    """, (status_str, status_str, ch_name, clean_id, str(raw_id), clean_id))

                    # Slower fallback only if direct indexed message_id did not match
                    if cur.rowcount == 0 and like_pat:
                        cur.execute("""
                            UPDATE dispatch_history
                            SET delivery_status = CASE
                                    WHEN delivery_status = 'read' AND %s = 'delivered' THEN 'read'
                                    ELSE %s
                                END,
                                channel = COALESCE(channel, %s),
                                message_id = COALESCE(message_id, %s)
                            WHERE message_ids LIKE %s OR message_preview LIKE %s
                        """, (status_str, status_str, ch_name, clean_id, like_pat, like_pat))

                    if status_str == 'failed':
                        cur.execute("""
                            SELECT voter_id, tenant_id FROM dispatch_history
                            WHERE message_id = %s OR message_id = %s
                        """, (str(raw_id), clean_id))
                        failed_rows = cur.fetchall()
                        if not failed_rows and like_pat:
                            cur.execute("""
                                SELECT voter_id, tenant_id FROM dispatch_history
                                WHERE message_ids LIKE %s OR message_preview LIKE %s
                            """, (like_pat, like_pat))
                            failed_rows = cur.fetchall()
                        for v_row in failed_rows:
                            v_id, t_id = v_row[0], v_row[1]
                            if v_id and t_id:
                                try:
                                    cur.execute(f"UPDATE voters_t{int(t_id)} SET is_dispatched = false WHERE voter_id = %s", (v_id,))
                                except Exception as terr:
                                    logger.warning("[WEBHOOK_REVERT_ERR] Failed updating tenant %s voter %s: %s", t_id, v_id, terr)
                            elif v_id:
                                try:
                                    cur.execute("UPDATE voters SET is_dispatched = false WHERE voter_id = %s", (v_id,))
                                except Exception:
                                    pass

            conn.commit()
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error("[WEBHOOK_PROCESS_ERR] %s", e, exc_info=True)
        return jsonify({'status': 'error', 'error': str(e)}), 400


@dispatch_bp.route('/api/prewarm_slip', methods=['POST'])
def api_prewarm_slip():
    """
    Lightweight, non-blocking pre-warm endpoint.
    Called when an operator opens the slip modal, starting card rendering
    and Meta media upload in the background while the operator enters the phone number.
    """
    try:
        payload = request.get_json(silent=True) or {}
        voter_ids = payload.get('voter_ids') or []
        slip_type = payload.get('slip_type') or 'Single'
        tenant, repo = _get_tenant()
        if not tenant:
            tenant = payload.get('tenant') or 'ward82'

        clean_ids = [int(x) for x in voter_ids if str(x).isdigit()]
        if not clean_ids:
            return jsonify({'success': False, 'error': 'No voter IDs provided'}), 400

        result = trigger_background_prewarm(tenant, slip_type, clean_ids)
        return jsonify({'success': True, 'prewarm': result}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# CAMPAIGN-WIDE AUTOMATED BULK VOTER SLIP BROADCAST APIS
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/bulk/estimate', methods=['POST'])
@audit_action('dispatch.bulk_estimate', entity_type='dispatch_batch')
def api_dispatch_bulk_estimate():
    """
    Pre-Flight Audience & Quota Estimator:
    Calculates eligible electors, phone reach, Smart Household vs Individual slip counts,
    and validates WhatsApp credit balance before launching.
    """
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({
            'success': False,
            'error': 'केवल व्यवस्थापक (Admin) को ही थोक पर्ची प्रसारण की अनुमति है (Restricted: Admin role required)'
        }), 403

    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500

    try:
        data = request.get_json(silent=True) or {}
        filters = data.get('filters', {})
        mode = data.get('mode', 'SMART_FAMILY')
        skip_recent_hours = int(data.get('skip_recent_hours', 48))

        from electionapp.services.bulk_slip_worker import estimate_bulk_dispatch
        est = estimate_bulk_dispatch(
            repo, tenant, filters=filters, mode=mode, skip_recent_hours=skip_recent_hours
        )
        # Remove internal items payload before sending response
        est.pop('_resolved_items', None)

        return jsonify({'success': True, 'estimation': est}), 200
    except Exception as e:
        current_app.logger.error("Error in api_dispatch_bulk_estimate: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/api/dispatch/bulk/start', methods=['POST'])
@audit_action('dispatch.bulk_start', entity_type='dispatch_batch')
def api_dispatch_bulk_start():
    """
    Launches a Campaign-Wide Bulk Slip Broadcast Job:
    Atomically holds quota credits, creates the job and queue in PostgreSQL,
    and triggers the decoupled background worker.
    """
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({
            'success': False,
            'error': 'केवल व्यवस्थापक (Admin) को ही थोक पर्ची प्रसारण की अनुमति है (Restricted: Admin role required)'
        }), 403

    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500

    try:
        data = request.get_json(silent=True) or {}
        filters = data.get('filters', {})
        mode = data.get('mode', 'SMART_FAMILY')
        skip_recent_hours = int(data.get('skip_recent_hours', 48))
        user_id = session.get('user_id')

        from electionapp.services.bulk_slip_worker import create_bulk_dispatch_job
        result = create_bulk_dispatch_job(
            repo, tenant, user_id=user_id, filters=filters, mode=mode, skip_recent_hours=skip_recent_hours
        )

        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        current_app.logger.error("Error in api_dispatch_bulk_start: %s", e, exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/api/dispatch/bulk/status', methods=['GET'])
@dispatch_bp.route('/api/dispatch/bulk/status/<job_id>', methods=['GET'])
def api_dispatch_bulk_status(job_id=None):
    """
    Returns realtime progress, delivery statistics, and live audit feed for a job.
    """
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({
            'success': False,
            'error': 'केवल व्यवस्थापक (Admin) को ही थोक पर्ची प्रसारण की अनुमति है (Restricted: Admin role required)'
        }), 403

    tenant, _ = _get_tenant()
    try:
        from electionapp.services.bulk_slip_worker import get_bulk_job_status
        status = get_bulk_job_status(tenant, job_id=job_id)
        return jsonify({'success': True, 'data': status}), 200
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/api/dispatch/bulk/pause/<job_id>', methods=['POST'])
@audit_action('dispatch.bulk_pause', entity_type='dispatch_batch')
def api_dispatch_bulk_pause(job_id):
    """Cooperatively pauses an active bulk broadcast."""
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    tenant, _ = _get_tenant()
    try:
        from electionapp.services.bulk_slip_worker import pause_bulk_job
        result = pause_bulk_job(tenant, job_id)
        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/api/dispatch/bulk/resume/<job_id>', methods=['POST'])
@audit_action('dispatch.bulk_resume', entity_type='dispatch_batch')
def api_dispatch_bulk_resume(job_id):
    """Resumes a paused bulk broadcast."""
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    tenant, repo = _get_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository not available'}), 500

    try:
        from electionapp.services.bulk_slip_worker import resume_bulk_job
        result = resume_bulk_job(repo, tenant, job_id)
        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/api/dispatch/bulk/cancel/<job_id>', methods=['POST'])
@audit_action('dispatch.bulk_cancel', entity_type='dispatch_batch')
def api_dispatch_bulk_cancel(job_id):
    """Cancels a bulk job permanently and refunds unspent WhatsApp credits."""
    user_role = session.get('role', 'operator')
    if user_role not in ('admin', 'super_admin'):
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403

    tenant, _ = _get_tenant()
    try:
        from electionapp.services.bulk_slip_worker import cancel_bulk_job
        result = cancel_bulk_job(tenant, job_id)
        status_code = 200 if result.get('success') else 400
        return jsonify(result), status_code
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@dispatch_bp.route('/cards/<path:filename>')
@dispatch_bp.route('/static/cards/<path:filename>')
def serve_slip_card_image(filename: str):
    """Serves generated HD slip card images for direct WhatsApp media delivery."""
    cards_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'generated_cards'))
    return send_from_directory(cards_dir, filename, max_age=86400)

