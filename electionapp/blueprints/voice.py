"""
electionapp/blueprints/voice.py
Voice Broadcast (OBD) Blueprint:
- GET /campaigns/voice    — Renders revamped 3-step voice_campaign.html dashboard
- GET /api/voice/balance  — Returns current voice quota balance
- GET /api/voice/sounds   — Returns approved sounds from PostgreSQL voice_audios (clean, pulse-rated)
- POST /api/voice/upload_audio — Transcodes to telecom WAV, generates opaque name, forwards to carrier
- GET /api/voice/audio/<int:audio_id> — Streams audio with HTTP Range seeking & tenant ownership verification
- POST /api/voice/estimate — Pre-calculates reach, pulse cost, credit balance for selected audience
- POST /api/voice/dispatch — Dispatches voice blast with fail-closed two-phase reservation & TRAI guards
"""

import os
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from flask import Blueprint, render_template, request, jsonify, session, current_app, g, redirect, send_file
import psycopg2.extras

from electionapp.audit import audit_action
from electionapp.config import load_tenant_config, is_ward_campaign, get_cached_ward_hierarchy
from electionapp.tenancy import resolve_tenant
from electionapp import voice_quota, voice_service
from electionapp.services import audio_converter
from electionapp.services.audio_converter import (
    calculate_pulse_credits,
    generate_carrier_sound_name,
    convert_to_telecom_wav,
)

logger = logging.getLogger("smartbooth.voice")

voice_bp = Blueprint('voice', __name__)


def is_within_trai_calling_window() -> bool:
    """TRAI calling window: 09:00 AM - 09:00 PM IST (09:00 to 21:00)."""
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(ist)
    return 9 <= now_ist.hour < 21


def check_trai_calling_window(data: Optional[Dict[str, Any]] = None) -> bool:
    """Checks TRAI calling window unless in test mode or bypass_window is supplied."""
    if current_app.config.get('TESTING', False):
        return True
    if request.args.get('bypass_window') in ('1', 'true', 'True'):
        return True
    if data and str(data.get('bypass_window', '')).lower() in ('1', 'true'):
        return True
    return is_within_trai_calling_window()


@voice_bp.before_request
def check_voice_enabled():
    """Fail-closed guard: Hide voice module endpoints when ENABLE_VOICE_MODULE is False."""
    if not current_app.config.get('ENABLE_VOICE_MODULE', False) and not current_app.config.get('TESTING'):
        if request.path.startswith('/api/voice'):
            return jsonify({'ok': False, 'error': 'Voice OBD module is currently disabled'}), 403
        tenant = request.args.get('tenant', '')
        return redirect(f"/?tenant={tenant}" if tenant else "/")


@voice_bp.route('/campaigns/voice', methods=['GET'])
def voice_campaign_page():
    """Renders the Voice Broadcast (OBD) campaign console."""
    tenant_slug, repo = resolve_tenant()
    try:
        campaign = load_tenant_config(tenant_slug)
    except Exception:
        campaign = current_app.config.get('CAMPAIGN', {})

    booths = campaign.get('booths', {})
    if not booths and repo and hasattr(repo, 'get_booths_dict'):
        try:
            booths = repo.get_booths_dict(tenant_slug) or {}
        except Exception:
            booths = {}

    wards, booths_with_wards = get_cached_ward_hierarchy(repo, tenant_slug, campaign)
    is_ward_roll = is_ward_campaign(campaign)

    user = getattr(g, 'current_user', {
        'id': session.get('user_id', 1),
        'role': session.get('role', 'super_admin'),
        'username': session.get('username', 'admin'),
        'name': session.get('full_name', 'Administrator')
    })

    return render_template(
        'voice_campaign.html',
        campaign=campaign,
        CAMPAIGN=campaign,
        CAMPAIGN_CONFIG=campaign,
        current_tenant=tenant_slug,
        booths=booths,
        BOOTHS=booths,
        wards=wards,
        WARDS=wards,
        booths_with_wards=booths_with_wards,
        BOOTHS_WITH_WARDS=booths_with_wards,
        IS_WARD_ELECTION=bool(is_ward_roll),
        active_page='voice',
        current_user=user
    )


@voice_bp.route('/api/voice/balance', methods=['GET'])
def api_voice_balance():
    """Returns JSON of current campaign quota balance."""
    tenant_slug, _ = resolve_tenant()
    try:
        quota = voice_quota.get_quota(tenant_slug)
        return jsonify({
            'success': True,
            'campaign': tenant_slug,
            'tenant': tenant_slug,
            'granted': quota.get('granted', 0),
            'available': quota.get('available', 0),
            'consumed': quota.get('consumed', 0),
            'reserved': quota.get('reserved', 0),
            'quota': quota
        })
    except Exception as e:
        logger.error("Failed to fetch voice quota for tenant %s: %s", tenant_slug, e)
        return jsonify({'success': False, 'error': str(e)}), 500


@voice_bp.route('/api/voice/sounds', methods=['GET'])
def api_voice_sounds():
    """Returns approved sounds list from PostgreSQL voice_audios (with fallback to gateway).
    Enriched with duration and calculate_pulse_credits. Zero HTML leaks.
    """
    tenant_slug, repo = resolve_tenant()
    sounds = []

    def _is_clean(text: Any) -> bool:
        if not text or not isinstance(text, str):
            return False
        t = text.strip()
        if not t:
            return False
        tl = t.lower()
        return not any(m in tl for m in ("<", ">", "<!doctype", "<html", "<body", "404 not found"))

    if repo and hasattr(repo, '_resolve_tenant'):
        try:
            tid, _ = repo._resolve_tenant(tenant_slug)
            with repo.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT audio_id, sound_name, carrier_sound_name, display_label, 
                               duration_seconds, status, original_filename
                        FROM voice_audios
                        WHERE tenant_id = %s
                        ORDER BY audio_id DESC
                    """, (tid,))
                    rows = cur.fetchall() or []
                    for r in rows:
                        s_name = r.get('sound_name') or ''
                        c_name = r.get('carrier_sound_name') or s_name
                        d_label = r.get('display_label') or s_name
                        if not _is_clean(s_name) and not _is_clean(d_label):
                            continue
                        dur = float(r.get('duration_seconds') or 0.0)
                        cpc = calculate_pulse_credits(dur)
                        sounds.append({
                            'audio_id': r.get('audio_id'),
                            'sound_name': s_name,
                            'carrier_sound_name': c_name,
                            'display_label': d_label,
                            'duration_seconds': dur,
                            'credits_per_call': cpc,
                            'status': r.get('status') or 'active',
                            'original_filename': r.get('original_filename') or ''
                        })
        except Exception as e:
            logger.warning("Could not fetch audios from voice_audios table: %s", e)

    # Fallback to gateway sounds for backward compatibility & tests
    if not sounds:
        try:
            gw_sounds = voice_service.get_sound_list()
            for s in gw_sounds:
                s_name = s.get('sound_name', '') if isinstance(s, dict) else str(s)
                c_name = str(s.get('carrier_sound_name') or s.get('id') or s_name) if isinstance(s, dict) else str(s)
                dur = float(s.get('duration_seconds') or 0.0) if isinstance(s, dict) else 0.0
                cpc = int(s.get('credit') or calculate_pulse_credits(dur)) if isinstance(s, dict) else 1
                if _is_clean(s_name):
                    sounds.append({
                        'audio_id': s.get('id') if isinstance(s, dict) else None,
                        'sound_name': s_name,
                        'carrier_sound_name': c_name,
                        'display_label': s_name,
                        'duration_seconds': dur,
                        'credits_per_call': cpc,
                        'status': s.get('status', 'active') if isinstance(s, dict) else 'active',
                        'original_filename': ''
                    })
        except Exception as gw_err:
            logger.warning("Fallback gateway sound fetch failed: %s", gw_err)

    return jsonify({'success': True, 'sounds': sounds})


@voice_bp.route('/api/voice/upload_audio', methods=['POST'])
@audit_action('voice.upload_audio', entity_type='voice_audio')
def api_voice_upload_audio():
    """Handles audio upload (file upload or browser mic recording blob),
    transcodes to telecom 8kHz mono WAV, generates opaque carrier sound name,
    uploads to carrier gateway, and records in voice_audios.
    """
    tenant_slug, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Database repository unavailable'}), 500

    sound_name = request.form.get('sound_name', '').strip()
    display_label = request.form.get('display_label', '').strip()
    if not sound_name and not display_label:
        return jsonify({'success': False, 'error': 'Sound name is required'}), 400

    if not sound_name:
        sound_name = display_label
    if not display_label:
        display_label = sound_name

    file = None
    for key in ('audio_file', 'file', 'recording', 'audio'):
        if key in request.files:
            file = request.files[key]
            break

    if not file:
        return jsonify({'success': False, 'error': 'Audio file is required'}), 400

    original_filename = file.filename or 'recorded_audio.webm'
    file_bytes = file.read()
    if not file_bytes:
        return jsonify({'success': False, 'error': 'Uploaded audio file is empty'}), 400

    base_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'data', tenant_slug, 'audio'))
    os.makedirs(base_dir, exist_ok=True)

    tid, _ = repo._resolve_tenant(tenant_slug)
    carrier_sound_name = audio_converter.generate_carrier_sound_name(tid, display_label)

    try:
        wav_bytes, output_path, duration_seconds, credits_per_call = audio_converter.convert_to_telecom_wav(
            file_bytes,
            output_dir=base_dir,
            output_filename=f"{carrier_sound_name}.wav"
        )
    except Exception as conv_err:
        logger.error("Audio transcoding failed for %s: %s", sound_name, conv_err)
        return jsonify({'success': False, 'error': f"Audio conversion failed: {conv_err}"}), 400

    success, message = voice_service.upload_sound(wav_bytes, carrier_sound_name)
    if not success:
        logger.error("Carrier sound upload failed: %s", message)
        return jsonify({'success': False, 'error': message}), 502

    # Save numeric carrier voice_file_id if returned
    if message and str(message).strip().isdigit():
        carrier_sound_name = str(message).strip()

    audio_id = None
    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO voice_audios 
                      (tenant_id, sound_name, carrier_sound_name, display_label, original_filename, 
                       duration_seconds, file_path, status, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING audio_id
                    """,
                    (tid, sound_name, carrier_sound_name, display_label, original_filename, 
                     duration_seconds, output_path, 'active', session.get('user_id'))
                )
                row = cur.fetchone()
                if row:
                    audio_id = row[0]
                conn.commit()
    except Exception as db_err:
        logger.warning("Could not persist voice_audios entry: %s", db_err)

    return jsonify({
        'success': True,
        'message': message,
        'audio_id': audio_id,
        'sound_name': sound_name,
        'carrier_sound_name': carrier_sound_name,
        'display_label': display_label,
        'duration_seconds': duration_seconds,
        'credits_per_call': credits_per_call
    })


@voice_bp.route('/api/voice/audio/<int:audio_id>', methods=['GET'])
def api_voice_get_audio(audio_id: int):
    """Streams the recorded/transcoded audio file for playback with HTTP Range seeking support
    and strict tenant ownership validation.
    """
    tenant_slug, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Database repository unavailable'}), 500

    tid, _ = repo._resolve_tenant(tenant_slug)
    row = None
    try:
        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT audio_id, tenant_id, sound_name, carrier_sound_name, file_path
                    FROM voice_audios
                    WHERE audio_id = %s
                """, (audio_id,))
                row = cur.fetchone()
    except Exception as e:
        logger.error("DB error looking up audio %s: %s", audio_id, e)
        return jsonify({'success': False, 'error': 'Database lookup failed'}), 500

    if not row:
        return jsonify({'success': False, 'error': 'Audio not found'}), 404

    if row['tenant_id'] != tid:
        return jsonify({'success': False, 'error': 'Unauthorized: Access to this audio is forbidden'}), 403

    file_path = row.get('file_path')
    if not file_path or not os.path.isfile(file_path):
        base_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'data', tenant_slug, 'audio'))
        candidate = os.path.join(base_dir, f"{row.get('carrier_sound_name', '')}.wav")
        if os.path.isfile(candidate):
            file_path = candidate
        else:
            return jsonify({'success': False, 'error': 'Audio file not found on disk'}), 404

    response = send_file(file_path, mimetype='audio/wav', conditional=True)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@voice_bp.route('/api/voice/estimate', methods=['POST'])
def api_voice_estimate():
    """Calculates call volume, unique mobile count, audio pulse credit cost,
    and total credits needed against available quota balance.
    """
    tenant_slug, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Database repository unavailable'}), 500

    data = request.get_json(silent=True) or request.form or {}
    target_type = data.get('target_type', 'all')
    target_booths = data.get('booths') or []
    part_no = data.get('part_no')
    if not target_booths and part_no is not None and str(part_no).strip():
        target_booths = [part_no]
    ward_no = data.get('ward_no')
    audio_id = data.get('audio_id')
    sound_name = data.get('sound_name')

    tid, _ = repo._resolve_tenant(tenant_slug)

    duration_seconds = 0.0
    credits_per_call = 1
    if audio_id:
        try:
            with repo.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT duration_seconds FROM voice_audios 
                        WHERE audio_id = %s AND tenant_id = %s
                    """, (audio_id, tid))
                    a_row = cur.fetchone()
                    if a_row:
                        duration_seconds = float(a_row.get('duration_seconds') or 0.0)
                        credits_per_call = calculate_pulse_credits(duration_seconds)
        except Exception as e:
            logger.warning("Estimate audio lookup failed: %s", e)
    elif sound_name:
        try:
            with repo.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT duration_seconds FROM voice_audios 
                        WHERE (sound_name = %s OR carrier_sound_name = %s) AND tenant_id = %s
                        ORDER BY audio_id DESC LIMIT 1
                    """, (sound_name, sound_name, tid))
                    a_row = cur.fetchone()
                    if a_row:
                        duration_seconds = float(a_row.get('duration_seconds') or 0.0)
                        credits_per_call = calculate_pulse_credits(duration_seconds)
        except Exception:
            pass

    user = getattr(g, 'current_user', None) or {}
    is_scoped = user.get('is_scoped') or session.get('is_scoped', False)

    where_clauses = ["tenant_id = %s", "contact_phone IS NOT NULL", "contact_phone != ''"]
    params: List[Any] = [tid]

    if target_type == 'unvoted':
        where_clauses.append("is_voted = FALSE")

    if target_booths and isinstance(target_booths, list):
        clean_b = [int(b) for b in target_booths if str(b).isdigit()]
        if clean_b:
            where_clauses.append("part_no = ANY(%s)")
            params.append(clean_b)

    if ward_no is not None and str(ward_no).strip() and str(ward_no).strip().upper() != 'ALL':
        if str(ward_no).strip().isdigit():
            where_clauses.append("ward_no = %s")
            params.append(int(ward_no))

    if is_scoped:
        scoped_w = [int(w) for w in (user.get('assigned_wards') or session.get('assigned_wards') or []) if str(w).isdigit()]
        if scoped_w:
            where_clauses.append("ward_no = ANY(%s)")
            params.append(scoped_w)

    target_voters_count = 0
    unique_mobiles_count = 0
    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT contact_phone
                    FROM voters
                    WHERE {' AND '.join(where_clauses)}
                """, tuple(params))
                rows = cur.fetchall() or []
                target_voters_count = len(rows)
                mob_set = set()
                for r in rows:
                    c = voice_service.extract_clean_mobiles(r[0])
                    if c:
                        mob_set.add(c)
                unique_mobiles_count = len(mob_set)
    except Exception as e:
        logger.error("Estimate query failed: %s", e)

    balance = 0
    try:
        quota = voice_quota.get_quota(tenant_slug)
        balance = quota.get('available', 0)
    except Exception:
        pass

    total_credits_needed = unique_mobiles_count * credits_per_call

    return jsonify({
        'success': True,
        'target_voters': target_voters_count,
        'unique_mobiles': unique_mobiles_count,
        'duration_seconds': duration_seconds,
        'credits_per_call': credits_per_call,
        'total_credits_needed': total_credits_needed,
        'balance': balance
    })


@voice_bp.route('/api/voice/dispatch', methods=['POST'])
@audit_action('voice.dispatch', entity_type='voice_campaign')
def api_voice_dispatch():
    """Receives {audio_id, sound_name, target_type, booths, limit, campaign_name}.
    Enforces TRAI calling window (09:00 - 21:00 IST).
    Resolves audio, duration, and credits_per_call server-side.
    Queries voters using tenant isolation (tenant_id), runs deduplication, reserves quota,
    dispatches in 500-call waves via voice_service, settles, and returns summary stats.
    """
    tenant_slug, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Database repository unavailable'}), 500

    data = request.get_json(silent=True) or request.form or {}

    # 0. TRAI Calling Window Enforcement (09:00 AM - 09:00 PM IST)
    if not check_trai_calling_window(data):
        return jsonify({
            'success': False,
            'error': 'TRAI Calling Window Restriction: Outbound voice calls are legally permitted only between 09:00 AM and 09:00 PM IST.'
        }), 403

    audio_id = data.get('audio_id')
    sound_name = str(data.get('sound_name') or '').strip()
    if not audio_id and not sound_name:
        return jsonify({'success': False, 'error': 'Please select an approved audio sound'}), 400

    tid, slug = repo._resolve_tenant(tenant_slug)

    # 1. Server-side audio resolution
    carrier_sound_name = sound_name
    duration_seconds = 0.0
    credits_per_call = 1

    if audio_id:
        try:
            with repo.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT audio_id, sound_name, carrier_sound_name, duration_seconds 
                        FROM voice_audios 
                        WHERE audio_id = %s AND tenant_id = %s
                    """, (audio_id, tid))
                    a_row = cur.fetchone()
                    if not a_row:
                        return jsonify({'success': False, 'error': f'Selected audio #{audio_id} not found'}), 404
                    sound_name = a_row.get('sound_name') or sound_name
                    carrier_sound_name = a_row.get('carrier_sound_name') or sound_name
                    duration_seconds = float(a_row.get('duration_seconds') or 0.0)
                    credits_per_call = calculate_pulse_credits(duration_seconds)
        except Exception as db_e:
            logger.error("Error resolving audio_id %s: %s", audio_id, db_e)
            return jsonify({'success': False, 'error': 'Database error resolving audio'}), 500
    elif sound_name:
        try:
            with repo.get_connection() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("""
                        SELECT audio_id, sound_name, carrier_sound_name, duration_seconds 
                        FROM voice_audios 
                        WHERE (sound_name = %s OR carrier_sound_name = %s) AND tenant_id = %s
                        ORDER BY audio_id DESC LIMIT 1
                    """, (sound_name, sound_name, tid))
                    a_row = cur.fetchone()
                    if a_row:
                        carrier_sound_name = a_row.get('carrier_sound_name') or sound_name
                        duration_seconds = float(a_row.get('duration_seconds') or 0.0)
                        credits_per_call = calculate_pulse_credits(duration_seconds)
                    else:
                        carrier_sound_name = sound_name
                        credits_per_call = 1
        except Exception:
            carrier_sound_name = sound_name
            credits_per_call = 1

    target_type = data.get('target_type', 'unvoted')
    target_booths = data.get('booths') or []
    part_no = data.get('part_no')
    if not target_booths and part_no is not None and str(part_no).strip():
        target_booths = [part_no]
    ward_no = data.get('ward_no')
    limit = int(data.get('limit', 500))
    limit = max(1, min(limit, 10000))
    campaign_name = data.get('campaign_name') or f"Voice Blast {tenant_slug}"

    # Enforce assigned operator booth/ward scoping
    user = getattr(g, 'current_user', None) or {}
    is_scoped = user.get('is_scoped') or session.get('is_scoped', False)
    if is_scoped:
        assigned_b = user.get('assigned_booths') or (
            [user['assigned_booth']] if user.get('assigned_booth') is not None else []
        ) or session.get('assigned_booths') or (
            [session['assigned_booth']] if session.get('assigned_booth') is not None else []
        )
        if assigned_b:
            clean_assigned_b = [int(ab) for ab in assigned_b if str(ab).isdigit()]
            if target_booths:
                target_booths = [b for b in target_booths if str(b).isdigit() and int(b) in clean_assigned_b]
                if not target_booths:
                    return jsonify({'success': False, 'error': 'आप केवल अपने आवंटित बूथ पर ही कॉल कर सकते हैं (Unauthorized booth scope)'}), 403
            else:
                target_booths = clean_assigned_b

    # 2. Query voters strictly partitioned by tenant_id
    voters = []
    try:
        with repo.get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                where_clauses = ["tenant_id = %s", "contact_phone IS NOT NULL", "contact_phone != ''"]
                params: List[Any] = [tid]

                if target_type == 'unvoted':
                    where_clauses.append("is_voted = FALSE")

                if target_booths and isinstance(target_booths, list):
                    clean_b = [int(b) for b in target_booths if str(b).isdigit()]
                    if clean_b:
                        where_clauses.append("part_no = ANY(%s)")
                        params.append(clean_b)

                if ward_no is not None and str(ward_no).strip() and str(ward_no).strip().upper() != 'ALL':
                    if str(ward_no).strip().isdigit():
                        where_clauses.append("ward_no = %s")
                        params.append(int(ward_no))

                if is_scoped:
                    if user.get('assigned_wards') or session.get('assigned_wards'):
                        scoped_w = [int(w) for w in (user.get('assigned_wards') or session.get('assigned_wards') or []) if str(w).isdigit()]
                        if scoped_w:
                            where_clauses.append("ward_no = ANY(%s)")
                            params.append(scoped_w)
                    if user.get('assigned_villages') or session.get('assigned_villages'):
                        scoped_v = [str(v).strip() for v in (user.get('assigned_villages') or session.get('assigned_villages') or []) if str(v).strip()]
                        if scoped_v:
                            where_clauses.append("village = ANY(%s)")
                            params.append(scoped_v)

                query = f"""
                    SELECT voter_id, name_hi, name_en, part_no, contact_phone
                    FROM voters
                    WHERE {' AND '.join(where_clauses)}
                    ORDER BY part_no, voter_id
                    LIMIT %s
                """
                params.append(limit * 2)  # fetch buffer before deduplication
                cur.execute(query, tuple(params))
                voters = [dict(r) for r in cur.fetchall() or []]
    except Exception as e:
        logger.error("Failed querying target voters for voice blast: %s", e)
        return jsonify({'success': False, 'error': f"Database query failed: {e}"}), 500

    if not voters:
        return jsonify({'success': False, 'error': 'No eligible voters with mobile numbers found'}), 404

    # 3. Deduplicate voter mobiles
    deduped_voters = voice_service.deduplicate_voter_mobiles(voters)
    if len(deduped_voters) > limit:
        deduped_voters = deduped_voters[:limit]

    mobile_numbers = [v['clean_mobile'] for v in deduped_voters]
    if not mobile_numbers:
        return jsonify({'success': False, 'error': 'No valid 10-digit mobile numbers after deduplication'}), 400

    # 4. Dispatch Voice Blast with pulse quota scaling
    success, message, stats = voice_service.dispatch_voice_blast(
        tenant_slug=tenant_slug,
        numbers_list=mobile_numbers,
        sound_name=carrier_sound_name,
        campaign_name=campaign_name,
        credits_per_call=credits_per_call
    )

    # 5. Record Campaign & Call Records in PostgreSQL
    if stats.get('dispatched', 0) > 0:
        try:
            with repo.get_connection() as conn:
                with conn.cursor() as cur:
                    gw_camp_id = stats['gateway_campaign_ids'][0] if stats.get('gateway_campaign_ids') else None
                    cur.execute(
                        """
                        INSERT INTO voice_campaigns 
                          (tenant_id, gateway_campaign_id, campaign_name, sound_name, total_target, dispatched_count, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING campaign_id
                        """,
                        (tid, gw_camp_id, campaign_name, carrier_sound_name, len(mobile_numbers), stats['dispatched'], session.get('user_id'))
                    )
                    row = cur.fetchone()
                    camp_id = row[0] if row else None

                    voter_by_mobile = {v['clean_mobile']: v.get('voter_id') for v in deduped_voters}
                    call_records_data = [
                        (tid, camp_id, mob, voter_by_mobile.get(mob), 'submitted', int(duration_seconds))
                        for mob in mobile_numbers[:stats['dispatched']]
                    ]
                    if call_records_data:
                        psycopg2.extras.execute_values(
                            cur,
                            """
                            INSERT INTO voice_call_records 
                              (tenant_id, campaign_id, mobile, voter_id, status, duration_seconds)
                            VALUES %s
                            """,
                            call_records_data
                        )
                    conn.commit()
        except Exception as rec_err:
            logger.error("Failed to record voice blast campaign in DB: %s", rec_err)

    if not success:
        return jsonify({'success': False, 'error': message, 'stats': stats}), 400

    return jsonify({
        'success': True,
        'message': message,
        'stats': stats,
        'target_count': len(mobile_numbers),
        'sound_name': sound_name,
        'carrier_sound_name': carrier_sound_name,
        'credits_per_call': credits_per_call,
        'duration_seconds': duration_seconds
    })


@voice_bp.route('/api/voice/provider', methods=['GET'])
def api_voice_provider_status():
    """Returns live CloudShope provider health, balance, and caller IDs."""
    try:
        balance = voice_service.get_balance()
        caller_ids = voice_service.get_caller_ids()
        return jsonify({
            'success': True,
            'provider': 'cloudshope',
            'balance': balance,
            'caller_ids': caller_ids
        })
    except Exception as e:
        logger.error("Error checking voice provider status: %s", e)
        return jsonify({'success': False, 'error': str(e)}), 500


@voice_bp.route('/api/voice/webhook/cloudshope', methods=['POST'])
def api_voice_cloudshope_webhook():
    """Receives real-time call event notifications & DLR from CloudShope."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    logger.info("CloudShope voice webhook received: %s", data)

    raw_phone = data.get('number') or data.get('mobile') or data.get('msisdn')
    mobile = voice_service.extract_clean_mobiles(raw_phone)
    status = (data.get('status') or data.get('call_status') or 'completed').lower()
    duration = int(data.get('duration') or data.get('billable_duration') or 0)
    gateway_call_id = str(data.get('call_id') or data.get('uuid') or data.get('obdEntryId') or '')

    tenant_slug, repo = resolve_tenant()
    if repo and mobile:
        try:
            tid, _ = repo._resolve_tenant(tenant_slug)
            with repo.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE voice_call_records
                        SET status = %s,
                            duration_seconds = %s,
                            gateway_call_id = COALESCE(NULLIF(%s, ''), gateway_call_id),
                            updated_at = NOW()
                        WHERE call_id = (
                            SELECT call_id FROM voice_call_records
                            WHERE mobile = %s AND (tenant_id = %s OR %s IS NULL)
                            ORDER BY call_id DESC LIMIT 1
                        )
                    """, (status, duration, gateway_call_id, mobile, tid, tid))
                    conn.commit()
        except Exception as e:
            logger.error("Error updating voice_call_records from webhook: %s", e)

    return jsonify({'success': True, 'message': 'Webhook processed'})
