"""
warroom.py
War Room Blueprint for Unified Multi-Tenant Voter Platform:
- War Room Dashboard UI (/warroom)
- Live vote marking (/api/mark_voted)
- Real-time turnout statistics (/api/turnout_stats)
- Get Out The Vote unvoted pipeline (/api/gotv)

Uses repository methods: mark_voted, get_turnout_stats, get_gotv_voters.
"""

import os
import subprocess
import tempfile
import datetime
from flask import Blueprint, render_template, jsonify, request, current_app, g, send_file, session
from electionapp.audit import audit_action
from electionapp.config import load_tenant_config, is_ward_campaign, get_cached_ward_hierarchy
from electionapp.tenancy import resolve_tenant

warroom_bp = Blueprint('warroom', __name__)


@warroom_bp.route('/warroom')
def warroom_dashboard():
    """
    Renders the election warroom analytics dashboard.
    """
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

    default_campaign_msg = campaign.get('campaign_message', '')
    user = getattr(g, 'current_user', {'id': 1, 'role': 'super_admin', 'username': 'admin', 'name': 'Administrator'})

    return render_template(
        'warroom.html',
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
        default_campaign_msg=default_campaign_msg,
        active_page='warroom',
        current_user=user
    )


@warroom_bp.route('/api/mark_voted', methods=['POST'])
@audit_action('voter.mark_voted', entity_type='voter')
def api_mark_voted():
    if session.get('role') == 'karyakarta':
        return jsonify({'success': False, 'error': 'कार्यकर्ता को वोट मार्क करने की अनुमति नहीं है (Restricted: Karyakartas cannot mark votes)'}), 403

    """
    Marks a voter as voted or unvoted.
    Accepts JSON body: { "voter_id": int, "voted": bool, "marked_by": str, "tenant": str }
    Also accepts legacy aliases: "is_voted".
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    data = request.get_json(silent=True) or request.form or {}
    voter_id = data.get('voter_id')
    if voter_id is None:
        return jsonify({'success': False, 'error': 'Missing required parameter: voter_id'}), 400

    try:
        voter_id = int(voter_id)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': f'Invalid voter_id: {voter_id}'}), 400

    # Determine voted boolean status
    voted_val = data.get('voted')
    if voted_val is None:
        voted_val = data.get('is_voted', True)

    if isinstance(voted_val, str):
        voted_bool = voted_val.strip().lower() in ('1', 'true', 'yes')
    else:
        voted_bool = bool(voted_val)

    user_role = session.get('role', '')
    if user_role == 'viewer':
        return jsonify({'success': False, 'error': 'दर्शक (Viewer) खाते को वोट मार्क करने की अनुमति नहीं है (Viewer role cannot mark votes)'}), 403

    assigned_booths = list(session.get('assigned_booths') or [])
    assigned_booth = session.get('assigned_booth')
    if assigned_booth is not None and assigned_booth not in assigned_booths:
        assigned_booths.append(assigned_booth)

    if assigned_booths and user_role not in ['super_admin', 'admin']:
        voter = repo.get_voter(tenant, voter_id)
        if voter:
            v_part = voter.get('part_no')
            if v_part is not None and int(v_part) not in assigned_booths:
                return jsonify({'success': False, 'error': f'आप केवल अपने अधिकृत बूथ ({assigned_booths}) के मतदाताओं को ही मार्क कर सकते हैं (Restricted to assigned booths)'}), 403

    marked_by = str(data.get('marked_by') or session.get('full_name') or 'Booth Agent').strip()

    result = repo.mark_voted(tenant, voter_id=voter_id, voted=voted_bool, marked_by=marked_by)
    if isinstance(result, bool):
        result = {'success': result, 'voter_id': voter_id, 'is_voted': voted_bool, 'tenant': tenant}
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code


@warroom_bp.route('/api/turnout_stats')
def api_turnout_stats():
    """
    Returns real-time turnout statistics, percentages, and booth-wise breakdowns.
    """
    repo = current_app.extensions.get('repo')
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    tenant = request.args.get('tenant') or getattr(g, 'tenant_slug', None) or request.cookies.get('voter_tenant') or getattr(repo, 'tenant_slug', '') or ''
    tenant = str(tenant).strip().lower()
    part_param = request.args.get('part_no') or request.args.get('part')
    part_no = int(part_param) if part_param and str(part_param).isdigit() else None

    stats = repo.get_turnout_stats(tenant, part_no=part_no)
    return jsonify(stats), 200


@warroom_bp.route('/api/gotv')
def api_gotv():
    """
    Get Out The Vote (GOTV): Returns unvoted voters for outreach before polls close.
    """
    repo = current_app.extensions.get('repo')
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    tenant = request.args.get('tenant') or getattr(g, 'tenant_slug', None) or request.cookies.get('voter_tenant') or getattr(repo, 'tenant_slug', '') or ''
    tenant = str(tenant).strip().lower()
    part_param = request.args.get('part_no') or request.args.get('part')
    part_no = int(part_param) if part_param and str(part_param).isdigit() else None
    category = request.args.get('category')
    limit = request.args.get('limit', default=100, type=int)

    curr_role = session.get('role', '')
    assigned_booths = list(session.get('assigned_booths') or [])
    assigned_booth = session.get('assigned_booth')
    if assigned_booth is not None and assigned_booth not in assigned_booths:
        assigned_booths.append(assigned_booth)

    if assigned_booths and curr_role not in ['super_admin', 'admin']:
        if part_no is not None and part_no not in assigned_booths:
            return jsonify({'success': True, 'part_no': part_no, 'category': category, 'total': 0, 'voters': [], 'tenant': tenant}), 200
        elif part_no is None and assigned_booths:
            part_no = assigned_booths[0]

    voters = repo.get_gotv_voters(tenant, part_no=part_no, category=category, limit=limit)
    return jsonify({
        'success': True,
        'part_no': part_no,
        'category': category,
        'total': len(voters),
        'voters': voters,
        'tenant': tenant
    }), 200


# ----------------------------------------------------
# VOTING DAY UNVOTED PDF GENERATION (PORTED)
# ----------------------------------------------------

@warroom_bp.route('/api/generate_unvoted_pdf', methods=['GET'])
def api_generate_unvoted_pdf():
    """
    Generates high-precision PDF document of unvoted voters for election day GOTV outreach.
    STRICTLY LOCAL EXECUTION: Does NOT connect to AWS EC2 or external clouds.
    Multi-tenant isolated with parameterized SQL queries.
    """
    tenant, repo = resolve_tenant()
    if not repo:
        return jsonify({'success': False, 'error': 'Repository extension not configured'}), 500

    tid, slug = repo._resolve_tenant(tenant)

    booth_param = request.args.get('booth', 'active').strip().lower()
    community_param = (request.args.get('community') or request.args.get('type') or 'all').strip()
    is_all = community_param.lower() in ('all', 'any', '', 'none')

    try:
        with repo.get_connection() as conn:
            with conn.cursor() as cur:
                # 1. Determine target parts for the active tenant
                if booth_param == 'active':
                    cur.execute("SELECT DISTINCT part_no FROM voters WHERE tenant_id = %s AND is_voted = TRUE ORDER BY part_no", (tid,))
                    active_parts = [r[0] for r in cur.fetchall()]
                    if not active_parts:
                        cur.execute("SELECT DISTINCT part_no FROM voters WHERE tenant_id = %s ORDER BY part_no", (tid,))
                        active_parts = [r[0] for r in cur.fetchall()]
                    target_parts = active_parts or [1]
                    suffix = "Active_Booths"
                elif booth_param in ('all', '99'):
                    cur.execute("SELECT DISTINCT part_no FROM voters WHERE tenant_id = %s ORDER BY part_no", (tid,))
                    target_parts = [r[0] for r in cur.fetchall()] or list(range(1, 10))
                    suffix = "All_Booths"
                else:
                    try:
                        b_num = int(booth_param)
                        target_parts = [b_num]
                        suffix = f"Booth_{b_num}"
                    except ValueError:
                        target_parts = [1]
                        suffix = "Booth_1"

                c_label = "समस्त मतदाता (All Voters)" if is_all else f"{community_param} मतदाता ({community_param} Voters)"
                c_prefix = "All" if is_all else community_param.title()
                filename = f"{slug.title()}_{c_prefix}_Unvoted_{suffix}.pdf"

                # 2. Build HTML sections booth-by-booth
                html_sections = []
                total_unvoted_all = 0

                tag_cond = "" if is_all else "AND (caste_tag ILIKE %s OR lower(caste_tag) = %s)"
                tag_params = () if is_all else (f"%{community_param}%", community_param.lower())

                for b in target_parts:
                    cur.execute("SELECT booth_name, booth_area FROM voters WHERE tenant_id = %s AND part_no = %s LIMIT 1", (tid, b))
                    b_row = cur.fetchone()
                    b_name = b_row[0] if b_row and b_row[0] else f"Booth {b}"
                    b_area = b_row[1] if b_row and b_row[1] else ""

                    cur.execute(f"SELECT count(*) FROM voters WHERE tenant_id = %s AND part_no = %s AND LOWER(COALESCE(status, '')) <> 'deleted' {tag_cond}", (tid, b) + tag_params)
                    tot = cur.fetchone()[0] or 0

                    cur.execute(f"SELECT count(*) FROM voters WHERE tenant_id = %s AND part_no = %s AND is_voted = TRUE AND LOWER(COALESCE(status, '')) <> 'deleted' {tag_cond}", (tid, b) + tag_params)
                    voted = cur.fetchone()[0] or 0

                    cur.execute(f"""
                        SELECT sl_no, name_hi, rel_name_hi, contact_phone, house_no, age, gender, name_en, rel_name_en
                        FROM voters
                        WHERE tenant_id = %s AND part_no = %s AND (is_voted = FALSE OR is_voted IS NULL)
                          AND LOWER(COALESCE(status, '')) <> 'deleted' {tag_cond}
                        ORDER BY sl_no ASC
                    """, (tid, b) + tag_params)
                    rows = cur.fetchall()
                    unvoted_cnt = len(rows)
                    total_unvoted_all += unvoted_cnt
                    pct = (voted * 100.0 / tot) if tot > 0 else 0.0

                    tbody_rows = []
                    for r in rows:
                        sl = r[0]
                        name = r[1] or r[7] or ''
                        rel = r[2] or r[8] or ''
                        phone = r[3] or ''
                        h_no = r[4] or '-'
                        age_gen = f"{r[5] or ''}/{r[6] or ''}"
                        phone_td = f'<td class="center phone-col">{phone}</td>' if phone else '<td class="center no-phone">-</td>'
                        tbody_rows.append(f"""
                          <tr>
                            <td class="center sl-col">{sl}</td>
                            <td class="name-col">{name}</td>
                            <td class="rel-col">{rel}</td>
                            <td class="center house-col">{h_no}</td>
                            <td class="center" style="color: #475569;">{age_gen}</td>
                            {phone_td}
                          </tr>
                        """)

                    section = f"""
                    <div class="booth-section">
                      <div class="header-card">
                        <div class="title-block">
                          <h1>{slug.title()} — भाग {b}: शेष {c_label} (जिन्होंने वोट नहीं डाला)</h1>
                          <p><strong>मतदान केंद्र:</strong> {b_name} | <strong>क्षेत्र:</strong> {b_area}</p>
                        </div>
                        <div class="stats-badge-group">
                          <div class="stat-badge">कुल: {tot}</div>
                          <div class="stat-badge success">वोट डाला: {voted} ({pct:.1f}%)</div>
                          <div class="stat-badge highlight">शेष: {unvoted_cnt}</div>
                        </div>
                      </div>
                      <table>
                        <thead>
                          <tr>
                            <th class="center" style="width: 42px;">क्र. सं.</th>
                            <th style="width: 175px;">मतदाता का नाम</th>
                            <th style="width: 175px;">संबंधी का नाम</th>
                            <th class="center" style="width: 45px;">मकान नं.</th>
                            <th class="center" style="width: 45px;">आयु/लिंग</th>
                            <th class="center" style="width: 140px;">मोबाइल नंबर</th>
                          </tr>
                        </thead>
                        <tbody>
                          {''.join(tbody_rows)}
                        </tbody>
                      </table>
                    </div>
                    """
                    html_sections.append(section)

        full_html = f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<title>{c_label} - शेष मतदाता सूची</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 9mm 9mm 11mm 9mm;
    @bottom-right {{
      content: counter(page) " / " counter(pages);
      font-size: 8pt;
      font-family: 'Segoe UI', sans-serif;
      color: #64748b;
    }}
  }}
  * {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}
  body {{
    font-family: 'Nirmala UI', 'Segoe UI', Arial, sans-serif;
    margin: 0;
    padding: 0;
    color: #0f172a;
    background: #fff;
    font-size: 8.5pt;
  }}
  .booth-section {{
    page-break-after: always;
  }}
  .booth-section:last-child {{
    page-break-after: avoid;
  }}
  .header-card {{
    border: 1.5px solid #1e3a8a;
    border-radius: 6px;
    padding: 7px 12px;
    margin-bottom: 7px;
    background: #f8fafc;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .title-block h1 {{
    margin: 0;
    font-size: 12pt;
    color: #1e3a8a;
    font-weight: 800;
  }}
  .title-block p {{
    margin: 2px 0 0 0;
    font-size: 8pt;
    color: #475569;
  }}
  .stats-badge-group {{
    display: flex;
    gap: 6px;
    text-align: right;
  }}
  .stat-badge {{
    background: #e2e8f0;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 7.5pt;
    font-weight: bold;
    color: #1e293b;
  }}
  .stat-badge.highlight {{
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #f87171;
  }}
  .stat-badge.success {{
    background: #dcfce7;
    color: #166534;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 8pt;
  }}
  thead {{
    display: table-header-group;
  }}
  tr {{
    page-break-inside: avoid;
  }}
  th {{
    background: #1e3a8a !important;
    color: #ffffff !important;
    font-weight: 700;
    padding: 4px 6px;
    border: 1px solid #0f172a;
    text-align: left;
    font-size: 8pt;
  }}
  th.center, td.center {{
    text-align: center;
  }}
  td {{
    padding: 3.5px 5px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
  }}
  tbody tr:nth-child(even) {{
    background-color: #f8fafc;
  }}
  .sl-col {{
    font-weight: 800;
    color: #1e3a8a;
    width: 44px;
  }}
  .name-col {{
    font-weight: 600;
    color: #0f172a;
    width: 175px;
  }}
  .rel-col {{
    color: #334155;
    width: 175px;
  }}
  .phone-col {{
    font-family: 'Consolas', monospace;
    font-weight: 700;
    color: #047857;
    background: #f0fdf4;
    width: 135px;
    font-size: 7.5pt;
  }}
  .house-col {{
    width: 45px;
    color: #475569;
  }}
  .no-phone {{
    color: #94a3b8;
  }}
</style>
</head>
<body>
{''.join(html_sections)}
</body>
</html>"""

        # Write to temp html and invoke Chrome to render PDF
        temp_dir = tempfile.gettempdir()
        temp_html_path = os.path.join(temp_dir, f"unvoted_{slug}_{int(datetime.datetime.now().timestamp())}.html")
        output_pdf_path = os.path.join(temp_dir, filename)

        with open(temp_html_path, 'w', encoding='utf-8') as f:
            f.write(full_html)

        chrome_exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        if not os.path.exists(chrome_exe):
            chrome_exe = "chrome"

        cmd = [
            chrome_exe,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={output_pdf_path}",
            temp_html_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if os.path.exists(temp_html_path):
            try:
                os.remove(temp_html_path)
            except Exception:
                pass

        if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
            return send_file(output_pdf_path, as_attachment=True, download_name=filename)
        else:
            return jsonify({'success': False, 'error': f'Failed to render PDF: {res.stderr}'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

