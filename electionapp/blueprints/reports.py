"""
reports.py
Voter Reports, Print & Export Center Blueprint for SmartBooth.
Provides:
  - GET  /api/reports/preview      - Live counts and preview records for dynamic UI tables
  - GET  /api/reports/export/excel - Formatted openpyxl .xlsx workbook with auto-filters & styling
  - GET  /api/reports/print_html   - Standalone printable A4 HTML (12-Up voter parchis & lists)
  - GET  /api/reports/export/pdf   - Server-side headless Chrome vector PDF generation
"""

import os
import io
import csv
import datetime
import tempfile
import subprocess
import psycopg2
import psycopg2.extras
from flask import Blueprint, jsonify, request, send_file, current_app, session, g, Response

from electionapp.tenancy import resolve_tenant
from electionapp.config import load_tenant_config, is_ward_campaign, parse_location_filter

reports_bp = Blueprint('reports', __name__)


def _get_tenant_and_repo():
    tenant, repo = resolve_tenant()
    if not repo:
        raise RuntimeError("Database repository extension is not configured")
    tid, slug = repo._resolve_tenant(tenant)
    return tenant, repo, tid, slug


def _query_db(conn, sql, params=(), fetch_one=False):
    """
    Executes a query and returns dictionaries for PostgreSQL.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(sql, params)
    if fetch_one:
        row = cur.fetchone()
        return dict(row) if row else None
    rows = cur.fetchall()
    return [dict(r) for r in rows]


def _build_reports_query(tid, args, is_super, assigned_booths, assigned_wards):
    """
    Constructs parameterized WHERE clauses and ORDER BY string based on incoming filters.
    """
    where_clauses = [
        "LOWER(COALESCE(status, '')) <> 'deleted'",
        "tenant_id = %s"
    ]
    params = [tid]

    # Preset macro shortcuts
    preset = (args.get('preset') or '').strip().lower()
    if preset == 'outside':
        where_clauses.append("(is_outside = TRUE OR is_outside = 1)")
    elif preset == 'unvoted':
        where_clauses.append("(is_voted IS NULL OR is_voted = FALSE OR is_voted = 0)")

    # Ward / Booth Filter
    part_param = args.get('part_no') or args.get('part') or args.get('booth')
    ward_param = args.get('ward_no') or args.get('ward')
    clean_ward, clean_part, clean_parts = parse_location_filter(ward_param, part_param)

    if clean_ward and clean_part:
        where_clauses.append("ward_no = %s AND part_no = %s")
        params.extend([clean_ward, clean_part])
    elif clean_parts:
        where_clauses.append("part_no = ANY(%s)")
        params.append(clean_parts)
    elif clean_part:
        where_clauses.append("part_no = %s")
        params.append(clean_part)
    elif clean_ward:
        where_clauses.append("ward_no = %s")
        params.append(clean_ward)

    # Caste / Community Tag Filter
    caste_param = (args.get('caste') or args.get('caste_tag') or '').strip()
    if caste_param and caste_param.lower() not in ('all', 'any'):
        where_clauses.append("(caste_tag LIKE %s OR name_hi LIKE %s OR name_en LIKE %s)")
        params.extend([f"%{caste_param}%", f"%{caste_param}%", f"%{caste_param}%"])

    # Supporter Sentiment Category Filter
    cat_param = args.get('category')
    if cat_param is not None and str(cat_param).lstrip('-').isdigit() and cat_param != 'all':
        cat_val = int(cat_param)
        if cat_val == 0:
            where_clauses.append("(voter_category IS NULL OR voter_category = 0)")
        else:
            where_clauses.append("voter_category = %s")
            params.append(cat_val)

    # Residence (Local vs Outside)
    res_param = (args.get('residence') or '').strip().lower()
    if res_param == 'local_only':
        where_clauses.append("(is_outside IS NULL OR is_outside = FALSE OR is_outside = 0)")
    elif res_param == 'outside_only':
        where_clauses.append("(is_outside = TRUE OR is_outside = 1)")

    # Mobile Presence Status
    mob_param = (args.get('mobile_status') or '').strip().lower()
    if mob_param == 'with_phone':
        where_clauses.append("contact_phone IS NOT NULL AND length(trim(contact_phone)) >= 10")
    elif mob_param == 'no_phone':
        where_clauses.append("(contact_phone IS NULL OR length(trim(contact_phone)) < 10)")

    # Voting Day Turnout Status
    vote_param = (args.get('voting_status') or '').strip().lower()
    if vote_param == 'voted':
        where_clauses.append("(is_voted = TRUE OR is_voted = 1)")
    elif vote_param == 'unvoted':
        where_clauses.append("(is_voted IS NULL OR is_voted = FALSE OR is_voted = 0)")

    # Gender Filter
    gender_param = (args.get('gender') or '').strip().upper()
    if gender_param in ('M', 'F', 'MALE', 'FEMALE', 'पु', 'म'):
        if gender_param in ('M', 'MALE', 'पु'):
            where_clauses.append("(LOWER(gender) LIKE 'm%' OR gender = 'पु')")
        else:
            where_clauses.append("(LOWER(gender) LIKE 'f%' OR gender = 'म')")

    # Age Min & Max
    age_min = args.get('age_min')
    if age_min and str(age_min).isdigit():
        where_clauses.append("age >= %s")
        params.append(int(age_min))

    age_max = args.get('age_max')
    if age_max and str(age_max).isdigit():
        where_clauses.append("age <= %s")
        params.append(int(age_max))

    # User Scoping (Field operators restricted to specific booths/wards)
    if not is_super:
        if assigned_booths:
            b_holders = ', '.join(['%s'] * len(assigned_booths))
            where_clauses.append(f"part_no IN ({b_holders})")
            params.extend(assigned_booths)
        if assigned_wards:
            w_holders = ', '.join(['%s'] * len(assigned_wards))
            where_clauses.append(f"ward_no IN ({w_holders})")
            params.extend(assigned_wards)

    # Sort Order
    sort_param = (args.get('sort_by') or 'serial').strip().lower()
    if sort_param == 'az_en':
        order_clause = "ORDER BY LOWER(TRIM(COALESCE(name_en, ''))) ASC, LOWER(TRIM(COALESCE(rel_name_en, ''))) ASC, age ASC"
    elif sort_param == 'az_hi':
        order_clause = "ORDER BY TRIM(COALESCE(name_hi, '')) ASC, TRIM(COALESCE(rel_name_hi, '')) ASC, age ASC"
    elif sort_param == 'house':
        order_clause = "ORDER BY COALESCE(ward_no, part_no) ASC, house_no ASC, sl_no ASC"
    elif sort_param == 'age':
        order_clause = "ORDER BY age ASC, sl_no ASC"
    else:  # default serial order
        order_clause = "ORDER BY COALESCE(ward_no, part_no) ASC, sl_no ASC"

    where_sql = " AND ".join(where_clauses)
    return where_sql, params, order_clause


# ---------------------------------------------------------------------------
# 1. API: Live Preview & Page Count Estimate
# ---------------------------------------------------------------------------

@reports_bp.route('/api/reports/preview')
def api_reports_preview():
    try:
        tenant, repo, tid, slug = _get_tenant_and_repo()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = curr_user.get('role') or session.get('role', '')
    is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or curr_user.get('tenant_slug') == '*')
    assigned_booths = list(curr_user.get('assigned_booths') or session.get('assigned_booths') or [])
    assigned_wards = list(curr_user.get('assigned_wards') or session.get('assigned_wards') or [])

    

    density = int(request.args.get('density', 12))
    limit = int(request.args.get('limit', 50))
    offset = int(request.args.get('offset', 0))

    try:
        with repo.get_connection() as conn:
            where_sql, params, order_clause = _build_reports_query(tid, request.args, is_super, assigned_booths, assigned_wards)
            id_col = "voter_id"

            # Count total matching
            count_res = _query_db(conn, f"SELECT COUNT(*) as total FROM voters WHERE {where_sql}", tuple(params), fetch_one=True)
            total_count = count_res['total'] if count_res else 0

            # Fetch preview rows
            preview_sql = f"""
                SELECT 
                    {id_col}, sl_no, COALESCE(ward_no, part_no) as ward_no, part_no, epic_no,
                    name_hi, name_en, rel_name_hi, rel_name_en, rel_type,
                    house_no, age, gender, contact_phone,
                    booth_name, booth_area,
                    caste_tag, voter_category, is_outside, is_voted, survey_status,
                    is_duplicate, duplicate_pair_sl, duplicate_pair_ward, duplicate_pair_name, duplicate_info, duplicate_group_id
                FROM voters
                WHERE {where_sql}
                {order_clause}
                LIMIT %s OFFSET %s
            """
            voters = _query_db(conn, preview_sql, tuple(params) + (limit, offset))

        page_estimate = max(1, (total_count + density - 1) // density) if total_count > 0 else 0

        return jsonify({
            'success': True,
            'total_matching': total_count,
            'page_estimate': page_estimate,
            'density': density,
            'limit': limit,
            'offset': offset,
            'voters': voters
        }), 200

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 2. API: Formatted Excel Workbook Export (.xlsx)
# ---------------------------------------------------------------------------

@reports_bp.route('/api/reports/export/excel')
def api_reports_excel():
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    try:
        tenant, repo, tid, slug = _get_tenant_and_repo()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    cfg = load_tenant_config(tenant)
    elec = cfg.get('election', {})
    cand = cfg.get('candidate', {})
    cand_name = cand.get('name_hi') or cand.get('name_en') or ''
    ward_title = elec.get('ward_name_hi') or elec.get('ward_name_en') or slug.title()
    app_title = elec.get('app_title', 'SmartBooth')

    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = curr_user.get('role') or session.get('role', '')
    is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or curr_user.get('tenant_slug') == '*')
    assigned_booths = list(curr_user.get('assigned_booths') or session.get('assigned_booths') or [])
    assigned_wards = list(curr_user.get('assigned_wards') or session.get('assigned_wards') or [])

    report_type = (request.args.get('type') or request.args.get('preset') or '').strip().lower()
    if report_type in ('counting_sheet', 'counting', 'matganana'):
        try:
            from electionapp.services.counting_sheet_service import generate_counting_sheet_excel
            with repo.get_connection() as conn:
                excel_stream = generate_counting_sheet_excel(conn, tid, slug, cfg)
                download_name = f"{slug.title()}_Counting_Sheet_{datetime.datetime.now().strftime('%Y%m%d')}.xlsx"
                return send_file(
                    excel_stream,
                    as_attachment=True,
                    download_name=download_name,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

    try:
        with repo.get_connection() as conn:
            where_sql, params, order_clause = _build_reports_query(tid, request.args, is_super, assigned_booths, assigned_wards)

            query_sql = f"""
                SELECT 
                    sl_no, COALESCE(ward_no, part_no) as ward_no, part_no, epic_no,
                    name_hi, name_en, rel_name_hi, rel_name_en, rel_type,
                    house_no, age, gender, contact_phone,
                    booth_name, booth_area,
                    caste_tag, voter_category, is_outside, is_voted,
                    is_duplicate, duplicate_pair_sl, duplicate_pair_ward, duplicate_pair_name, duplicate_info, duplicate_group_id, survey_status
                FROM voters
                WHERE {where_sql}
                {order_clause}
                LIMIT 50000
            """
            rows = _query_db(conn, query_sql, tuple(params))

        headers = [
            "क्र.सं.", "वार्ड / भाग", "मतदाता क्र.सं.", "पहचान पत्र (EPIC)",
            "मतदाता नाम (हिन्दी)", "मतदाता नाम (English)", "संबंधी का नाम", "संबंध",
            "मकान नं.", "आयु", "लिंग", "मोबाइल नंबर",
            "समुदाय (Caste)", "समर्थक श्रेणी", "प्रवासी स्थिति", "डुप्लीकेट स्थिति", "मतदान स्थिति", "मतदान केंद्र"
        ]

        category_map = {
            100: "पक्के समर्थक (100)",
            50: "संशय / 50-50",
            0: "सामान्य / अनमार्कड",
            -1: "विरोधी (-1)"
        }

        # If explicit format=csv requested, stream lightweight UTF-8 CSV
        if request.args.get('format') == 'csv':
            def generate_csv_stream():
                output = io.StringIO()
                # Yield UTF-8 Byte Order Mark (BOM) so Excel renders Hindi text cleanly
                yield '\ufeff'
                writer = csv.writer(output)
                writer.writerow(headers)
                yield output.getvalue()
                output.seek(0)
                output.truncate(0)

                for idx, r in enumerate(rows, 1):
                    cat_label = category_map.get(r.get('voter_category'), 'सामान्य')
                    outside_str = "प्रवासी" if r.get('is_outside') else "स्थानीय"
                    voted_str = "वोटेड" if r.get('is_voted') else "बाकी"
                    is_dup = bool(r.get('is_duplicate')) or (r.get('survey_status') == 'DUPLICATE')
                    dup_pair_sl = r.get('duplicate_pair_sl')
                    dup_pair_ward = r.get('duplicate_pair_ward')
                    dup_pair_name = r.get('duplicate_pair_name') or ''
                    dup_info = r.get('duplicate_info') or ''

                    if is_dup:
                        if dup_pair_sl:
                            loc = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""
                            name_part = f" ({dup_pair_name})" if dup_pair_name else ""
                            dup_str = f"डुप्लीकेट (मूल: {loc}क्र. #{dup_pair_sl}{name_part})"
                        elif dup_info:
                            dup_str = f"डुप्लीकेट ({dup_info})"
                        else:
                            dup_str = "डुप्लीकेट"
                    elif dup_pair_sl:
                        loc = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""
                        name_part = f" ({dup_pair_name})" if dup_pair_name else ""
                        dup_str = f"दोहराव मिला (प्रतिलिपि: {loc}क्र. #{dup_pair_sl}{name_part})"
                    else:
                        dup_str = "सामान्य"
                    row_data = [
                        idx,
                        r.get('ward_no') or r.get('part_no'),
                        r.get('sl_no'),
                        r.get('epic_no') or '-',
                        r.get('name_hi') or '',
                        r.get('name_en') or '',
                        r.get('rel_name_hi') or r.get('rel_name_en') or '',
                        r.get('rel_type') or 'पिता',
                        r.get('house_no') or '-',
                        r.get('age') or '',
                        r.get('gender') or '',
                        str(r.get('contact_phone') or ''),
                        r.get('caste_tag') or '-',
                        cat_label,
                        outside_str,
                        dup_str,
                        voted_str,
                        r.get('booth_name') or ''
                    ]
                    writer.writerow(row_data)
                    yield output.getvalue()
                    output.seek(0)
                    output.truncate(0)

            csv_filename = f"{slug.title()}_Voter_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            return Response(
                generate_csv_stream(),
                mimetype='text/csv; charset=utf-8',
                headers={'Content-Disposition': f'attachment; filename="{csv_filename}"'}
            )

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Voter Report"

        # Report Header Banner
        preset_label = request.args.get('preset', 'voter_list').title()
        gen_time = datetime.datetime.now().strftime('%d-%m-%Y %I:%M %p')

        ws.append([f"{ward_title} — {app_title} {preset_label} Report"])
        ws.append([f"प्रत्याशी: {cand_name} | कुल मतदाता: {len(rows)} | रिपोर्ट निर्माण तिथि: {gen_time}"])
        ws.append([])

        ws.append(headers)

        header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
        header_font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        thin_border = Border(
            left=Side(style='thin', color='CBD5E1'),
            right=Side(style='thin', color='CBD5E1'),
            top=Side(style='thin', color='CBD5E1'),
            bottom=Side(style='thin', color='CBD5E1')
        )

        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=4, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = thin_border

        ws.row_dimensions[4].height = 24

        zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
        is_large_dataset = len(rows) > 1500

        for idx, r in enumerate(rows, 1):
            cat_label = category_map.get(r.get('voter_category'), 'सामान्य')
            outside_str = "✈ प्रवासी" if r.get('is_outside') else "स्थानीय"
            voted_str = "✓ वोटेड" if r.get('is_voted') else "बाकी"
            is_dup = bool(r.get('is_duplicate')) or (r.get('survey_status') == 'DUPLICATE')
            dup_pair_sl = r.get('duplicate_pair_sl')
            dup_pair_ward = r.get('duplicate_pair_ward')
            dup_pair_name = r.get('duplicate_pair_name') or ''
            dup_info = r.get('duplicate_info') or ''

            if is_dup:
                if dup_pair_sl:
                    loc = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""
                    name_part = f" ({dup_pair_name})" if dup_pair_name else ""
                    dup_str = f"⚠️ डुप्लीकेट (मूल: {loc}क्र. #{dup_pair_sl}{name_part})"
                elif dup_info:
                    dup_str = f"⚠️ डुप्लीकेट ({dup_info})"
                else:
                    dup_str = "⚠️ डुप्लीकेट"
            elif dup_pair_sl:
                loc = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""
                name_part = f" ({dup_pair_name})" if dup_pair_name else ""
                dup_str = f"ℹ️ दोहराव मिला (प्रतिलिपि: {loc}क्र. #{dup_pair_sl}{name_part})"
            else:
                dup_str = "सामान्य"

            row_data = [
                idx,
                r.get('ward_no') or r.get('part_no'),
                r.get('sl_no'),
                r.get('epic_no') or '-',
                r.get('name_hi') or '',
                r.get('name_en') or '',
                r.get('rel_name_hi') or r.get('rel_name_en') or '',
                r.get('rel_type') or 'पिता',
                r.get('house_no') or '-',
                r.get('age') or '',
                r.get('gender') or '',
                str(r.get('contact_phone') or ''),
                r.get('caste_tag') or '-',
                cat_label,
                outside_str,
                dup_str,
                voted_str,
                r.get('booth_name') or ''
            ]
            ws.append(row_data)

            # Skip cell-by-cell micro-styling loop on large datasets (>1500 rows) to prevent 30s CPU lock
            if not is_large_dataset:
                row_num = 4 + idx
                is_even = (idx % 2 == 0)

                for col_idx in range(1, len(headers) + 1):
                    cell = ws.cell(row=row_num, column=col_idx)
                    cell.border = thin_border
                    cell.font = Font(name="Arial", size=9)
                    if is_even:
                        cell.fill = zebra_fill
                    if col_idx in (1, 2, 3, 10, 11):
                        cell.alignment = Alignment(horizontal="center")
                    elif col_idx == 12:
                        cell.number_format = '@'

        # Auto-fit column widths (only on moderate sized sheets to prevent scanning millions of cells)
        if not is_large_dataset:
            for col in ws.columns:
                max_len = max((len(str(cell.value or '')) for cell in col), default=10)
                col_letter = get_column_letter(col[0].column)
                ws.column_dimensions[col_letter].width = max(max_len + 3, 11)

        ws.auto_filter.ref = f"A4:{get_column_letter(len(headers))}{len(rows) + 4}"

        out_io = io.BytesIO()
        wb.save(out_io)
        out_io.seek(0)

        filename = f"{slug.title()}_Voter_Report_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(
            out_io,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 3. API: Standalone Printable A4 HTML (Parchi & Table)
# ---------------------------------------------------------------------------

@reports_bp.route('/api/reports/print_html')
def api_reports_print_html():
    try:
        tenant, repo, tid, slug = _get_tenant_and_repo()
    except Exception as e:
        return f"<h1>Error: {e}</h1>", 500

    cfg = load_tenant_config(tenant)
    elec = cfg.get('election', {})
    cand = cfg.get('candidate', {})
    cand_name = cand.get('name_hi') or cand.get('name_en') or ''
    cand_symbol = cand.get('symbol') or elec.get('symbol') or '🚜 ट्रैक्टर'
    ward_title = elec.get('ward_name_hi') or elec.get('ward_name_en') or slug.title()
    app_title = elec.get('app_title', 'SmartBooth')

    curr_user = getattr(g, 'current_user', {}) or {}
    user_role = curr_user.get('role') or session.get('role', '')
    is_super = (user_role == 'super_admin' or session.get('role') == 'super_admin' or curr_user.get('tenant_slug') == '*')
    assigned_booths = list(curr_user.get('assigned_booths') or session.get('assigned_booths') or [])
    assigned_wards = list(curr_user.get('assigned_wards') or session.get('assigned_wards') or [])

    

    report_type = (request.args.get('type') or request.args.get('preset') or 'parchi').strip().lower()
    density = int(request.args.get('density', 12))  # 12 or 10

    try:
        with repo.get_connection() as conn:
            where_sql, params, order_clause = _build_reports_query(tid, request.args, is_super, assigned_booths, assigned_wards)

            voters = _query_db(conn, f"""
                SELECT 
                    sl_no, COALESCE(ward_no, part_no) as ward_no, part_no, epic_no,
                    name_hi, name_en, rel_name_hi, rel_name_en, rel_type,
                    house_no, age, gender, contact_phone,
                    booth_name, booth_area,
                    caste_tag, voter_category, is_outside, is_voted,
                    is_duplicate, duplicate_pair_sl, duplicate_pair_ward, duplicate_pair_name, duplicate_info, duplicate_group_id, survey_status
                FROM voters
                WHERE {where_sql}
                {order_clause}
                LIMIT 20000
            """, tuple(params))

        if report_type in ('counting_sheet', 'counting', 'matganana'):
            from electionapp.services.counting_sheet_service import render_counting_sheet_html
            html_output = render_counting_sheet_html(conn, tid, slug, cfg)
            return html_output, 200, {'Content-Type': 'text/html; charset=utf-8'}
        elif report_type == 'parchi':
            html_output = _generate_parchi_print_html(voters, ward_title, cand_name, cand_symbol, density)
        elif report_type == 'mobile_sheet':
            html_output = _generate_mobile_sheet_print_html(voters, ward_title, cand_name)
        else:
            html_output = _generate_table_print_html(voters, ward_title, cand_name, report_type)

        return html_output, 200, {'Content-Type': 'text/html; charset=utf-8'}

    except Exception as e:
        return f"<h1>Error generating report: {e}</h1>", 500


def _generate_parchi_print_html(voters, ward_title, cand_name, cand_symbol, density=12):
    """
    Renders pixel-perfect 12-Up (2x6) or 10-Up (2x5) A4 pages with dashed scissor cutting guides.
    """
    rows_per_page = 6 if density == 12 else 5
    per_page = density
    total_pages = max(1, (len(voters) + per_page - 1) // per_page) if voters else 1

    cand_chip = f"<span>{cand_symbol}</span>" if cand_symbol else f"<span>{cand_name}</span>"

    pages_html = []
    for p in range(total_pages):
        page_voters = voters[p * per_page : (p + 1) * per_page]
        cards_html = []

        for v in page_voters:
            n_hi = v.get('name_hi') or v.get('name_en') or ''
            n_en = f"({v.get('name_en')})" if (v.get('name_en') and v.get('name_en') != n_hi) else ""
            r_hi = v.get('rel_name_hi') or v.get('rel_name_en') or '-'
            r_type = v.get('rel_type') or 'पिता/पति'
            gen = 'पु' if str(v.get('gender', '')).upper().startswith('M') or v.get('gender') == 'पु' else 'म'
            age_str = f"{v.get('age') or ''} वर्ष / {gen}"
            booth_txt = v.get('booth_name') or 'राजकीय विद्यालय'
            epic_txt = v.get('epic_no') or '-'
            sl_no = v.get('sl_no') or '-'
            part_no = v.get('ward_no') or v.get('part_no') or '-'

            is_dup = bool(v.get('is_duplicate')) or (v.get('survey_status') == 'DUPLICATE')
            dup_pair_sl = v.get('duplicate_pair_sl')
            dup_pair_ward = v.get('duplicate_pair_ward')
            dup_pair_name = v.get('duplicate_pair_name') or ''
            loc_txt = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""

            if is_dup and dup_pair_sl:
                dup_parchi_banner = f'<div class="dup-parchi-pill">⚠️ डुप्लीकेट (मूल: {loc_txt}क्र. #{dup_pair_sl})</div>'
            elif is_dup:
                dup_parchi_banner = '<div class="dup-parchi-pill">⚠️ डुप्लीकेट पर्ची</div>'
            elif dup_pair_sl:
                dup_parchi_banner = f'<div class="dup-parchi-pill" style="background:#eff6ff; color:#1d4ed8; border-color:#93c5fd;">ℹ️ दोहराव (प्रतिलिपि: {loc_txt}क्र. #{dup_pair_sl})</div>'
            else:
                dup_parchi_banner = ''

            card = f"""
            <div class="parchi-card">
              <span class="cut-tag">✂ कटिंग लाइन</span>
              <div class="slip-header">
                <div class="slip-title">🗳️ {ward_title}</div>
                <div class="candidate-pill">{cand_chip}</div>
              </div>

              <div class="meta-banner">
                <div class="serial-badge"><span>क्र. सं.</span> <strong>{sl_no}</strong></div>
                <div class="part-badge">वार्ड / भाग: <strong>{part_no}</strong></div>
                <div class="epic-badge">{epic_txt}</div>
              </div>
              {dup_parchi_banner}

              <div class="voter-info">
                <div class="info-row">
                  <span class="label">मतदाता:</span>
                  <span class="value">{n_hi}</span>
                  <span class="sub-val">{n_en}</span>
                </div>
                <div class="info-row">
                  <span class="label">{r_type}:</span>
                  <span class="value">{r_hi}</span>
                </div>
                <div class="inline-grid">
                  <div class="info-row">
                    <span class="label" style="width:42px;">मकान नं.:</span>
                    <span class="value" style="font-size:7.2pt;">{v.get('house_no') or '-'}</span>
                  </div>
                  <div class="info-row">
                    <span class="label" style="width:42px;">आयु/लिंग:</span>
                    <span class="value" style="font-size:7.2pt;">{age_str}</span>
                  </div>
                </div>
              </div>

              <div class="booth-block">
                <strong>मतदान केंद्र:</strong> {booth_txt}
              </div>

              <div class="slip-footer">
                <span>⏰ समय: प्रातः 7:00 से सायं 5:00 बजे</span>
                <span class="appeal">★ पहचान पत्र साथ लाएं ★</span>
              </div>
            </div>
            """
            cards_html.append(card)

        # Pad with empty placeholder cards if last page has fewer cards
        while len(cards_html) < per_page:
            cards_html.append('<div class="parchi-card empty-card"></div>')

        page_div = f"""
        <div class="a4-sheet grid-density-{density}">
          {''.join(cards_html)}
        </div>
        """
        pages_html.append(page_div)

    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<title>{ward_title} - मतदाता पर्ची (A4 Cut Sheet)</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 4mm;
  }}
  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}
  body {{
    font-family: 'Segoe UI', 'Nirmala UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
    background: #fff;
    color: #0f172a;
  }}
  .a4-sheet {{
    width: 202mm;
    height: 289mm;
    max-height: 289mm;
    padding: 3mm;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2.2mm 3.5mm;
    page-break-after: always;
    break-after: page;
    position: relative;
    overflow: hidden;
  }}
  .a4-sheet:last-child {{
    page-break-after: auto;
    break-after: auto;
  }}
  .grid-density-12 {{
    grid-template-rows: repeat(6, 1fr);
  }}
  .grid-density-10 {{
    grid-template-rows: repeat(5, 1fr);
  }}
  .parchi-card {{
    border: 1.2px dashed #64748b;
    border-radius: 4px;
    padding: 3.5px 5.5px;
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    background: #ffffff;
    position: relative;
    font-size: 7.2pt;
    line-height: 1.2;
    overflow: hidden;
  }}
  .empty-card {{
    border: 1px dashed #e2e8f0;
    visibility: hidden;
  }}
  .cut-tag {{
    position: absolute;
    top: -7px;
    right: 6px;
    font-size: 6.5pt;
    color: #64748b;
    background: #ffffff;
    padding: 0 2px;
  }}
  .slip-header {{
    border-bottom: 1.2px solid #0f172a;
    padding-bottom: 2px;
    margin-bottom: 2px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }}
  .slip-title {{
    font-size: 7pt;
    font-weight: 800;
    color: #0f172a;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .candidate-pill {{
    font-size: 6.8pt;
    font-weight: 800;
    color: #1e3a8a;
    background: #eff6ff;
    border: 0.8px solid #93c5fd;
    border-radius: 3px;
    padding: 0.5px 4px;
    display: flex;
    align-items: center;
    gap: 2px;
  }}
  .meta-banner {{
    background: #f8fafc;
    border: 0.8px solid #cbd5e1;
    border-radius: 3px;
    padding: 1.5px 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 2px;
  }}
  .dup-parchi-pill {{
    background: #fee2e2;
    border: 0.8px solid #f87171;
    color: #991b1b;
    font-size: 6.4pt;
    font-weight: 800;
    text-align: center;
    border-radius: 2px;
    padding: 1px 3px;
    margin-bottom: 2px;
  }}
  .serial-badge {{
    background: #0f172a;
    color: #ffffff;
    font-size: 8pt;
    font-weight: 900;
    padding: 0.5px 5px;
    border-radius: 2px;
    display: inline-flex;
    align-items: center;
    gap: 2px;
  }}
  .part-badge {{
    font-size: 7pt;
    font-weight: 700;
    color: #1e293b;
  }}
  .epic-badge {{
    font-family: monospace;
    font-size: 7pt;
    font-weight: 800;
    color: #0f172a;
    letter-spacing: 0.2px;
  }}
  .voter-info {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 1px;
    margin-bottom: 2px;
  }}
  .info-row {{
    display: flex;
    align-items: baseline;
    font-size: 7.2pt;
  }}
  .label {{
    color: #475569;
    font-size: 6.8pt;
    font-weight: 700;
    width: 52px;
    flex-shrink: 0;
  }}
  .value {{
    color: #0f172a;
    font-weight: 800;
    font-size: 7.8pt;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .sub-val {{
    font-size: 6.5pt;
    color: #475569;
    font-weight: 600;
    margin-left: 3px;
  }}
  .inline-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2px;
  }}
  .booth-block {{
    background: #f8fafc;
    border-left: 2px solid #0f172a;
    padding: 1.5px 3.5px;
    font-size: 6.4pt;
    color: #1e293b;
    margin-bottom: 2px;
    line-height: 1.15;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .booth-block strong {{
    color: #0f172a;
  }}
  .slip-footer {{
    border-top: 0.6px solid #cbd5e1;
    padding-top: 1.5px;
    font-size: 6.2pt;
    color: #475569;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-weight: 700;
  }}
  .appeal {{
    color: #166534;
    font-weight: 800;
  }}
</style>
</head>
<body>
{''.join(pages_html)}
<script>
window.onafterprint = function() {{
  if (window.opener) window.close();
}};
</script>
</body>
</html>"""


def _generate_mobile_sheet_print_html(voters, ward_title, cand_name):
    """
    Field surveyor mobile collection sheet with dotted write-in boxes.
    """
    rows_html = []
    for idx, v in enumerate(voters, 1):
        n_hi = v.get('name_hi') or v.get('name_en') or ''
        r_hi = v.get('rel_name_hi') or v.get('rel_name_en') or ''
        phone = str(v.get('contact_phone') or '')
        is_dup = bool(v.get('is_duplicate')) or (v.get('survey_status') == 'DUPLICATE')
        dup_pair_sl = v.get('duplicate_pair_sl')
        dup_pair_ward = v.get('duplicate_pair_ward')
        loc_str = f"W{dup_pair_ward} " if dup_pair_ward else ""

        if is_dup and dup_pair_sl:
            dup_tag = f'<span style="font-size:6.8pt; color:#b91c1c; font-weight:800; background:#fee2e2; border:0.5px solid #fca5a5; border-radius:2px; padding:0 3px; margin-left:3px;">[DUP: {loc_str}#{dup_pair_sl}]</span>'
        elif is_dup:
            dup_tag = '<span style="font-size:6.8pt; color:#b91c1c; font-weight:800; background:#fee2e2; border:0.5px solid #fca5a5; border-radius:2px; padding:0 3px; margin-left:3px;">[DUP]</span>'
        elif dup_pair_sl:
            dup_tag = f'<span style="font-size:6.8pt; color:#2563eb; font-weight:800; background:#eff6ff; border:0.5px solid #93c5fd; border-radius:2px; padding:0 3px; margin-left:3px;">[PAIR: {loc_str}#{dup_pair_sl}]</span>'
        else:
            dup_tag = ''

        if phone and len(phone) >= 10:
            phone_box = f'<span class="bold" style="font-family:monospace; font-size:9pt;">{phone[-10:]}</span>'
        else:
            phone_box = '<div style="display:flex; justify-content:center; gap:2px;"><span style="border-bottom:1px dotted #64748b; width:120px; height:14px; display:inline-block;"></span></div>'

        rows_html.append(f"""
        <tr>
          <td class="center">{v.get('sl_no') or idx}</td>
          <td class="bold">{n_hi} {dup_tag}</td>
          <td>{r_hi}</td>
          <td class="center">{v.get('house_no') or '-'}</td>
          <td class="center">{v.get('age') or ''}/{v.get('gender') or ''}</td>
          <td class="center" style="width:145px;">{phone_box}</td>
          <td class="center" style="width:50px;"><div style="border:1px solid #94a3b8; width:14px; height:14px; margin:auto; border-radius:2px;"></div></td>
        </tr>
        """)

    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<title>{ward_title} - मोबाइल नंबर प्रविष्टि सर्वे पत्रक</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 8mm 6mm;
  }}
  * {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}
  body {{
    font-family: 'Segoe UI', 'Nirmala UI', sans-serif;
    color: #0f172a;
    background: #fff;
    margin: 0;
  }}
  .header-card {{
    border-bottom: 2px solid #1e3a8a;
    padding-bottom: 4px;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }}
  .title {{
    font-size: 13pt;
    font-weight: 900;
    color: #1e3a8a;
  }}
  .meta {{
    font-size: 8pt;
    color: #475569;
    font-weight: bold;
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
    font-weight: bold;
    padding: 4px 6px;
    border: 1px solid #0f172a;
    font-size: 7.5pt;
  }}
  td {{
    padding: 3.5px 5px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
  }}
  tr:nth-child(even) td {{
    background-color: #f8fafc;
  }}
  .center {{ text-align: center; }}
  .bold {{ font-weight: bold; color: #0f172a; }}
</style>
</head>
<body>
<div class="header-card">
  <div>
    <div class="title">📱 {ward_title} — मोबाइल नंबर प्रविष्टि सर्वे पत्रक</div>
    <div class="meta">प्रत्याशी: {cand_name} | कुल मतदाता: {len(voters)} | खाली कॉलम में 10 अंकों का मोबाइल नंबर दर्ज करें</div>
  </div>
  <div style="font-size:8pt; font-weight:bold; color:#1e3a8a;">
    सर्वे दिनांक: ______________
  </div>
</div>
<table>
  <thead>
    <tr>
      <th style="width:40px;">क्र.सं.</th>
      <th style="width:160px;">मतदाता का नाम</th>
      <th style="width:150px;">पिता / पति का नाम</th>
      <th style="width:45px;">मकान</th>
      <th style="width:45px;">आयु/लिंग</th>
      <th>मोबाइल नंबर (लिखें)</th>
      <th>समर्थक</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
</body>
</html>"""


def _generate_table_print_html(voters, ward_title, cand_name, report_type):
    """
    Standard sequential or alphabetical voter list table.
    """
    rows_html = []
    for idx, v in enumerate(voters, 1):
        n_hi = v.get('name_hi') or v.get('name_en') or ''
        n_en = f"<span style='color:#64748b; font-size:7pt;'>({v.get('name_en')})</span>" if v.get('name_en') else ""
        r_hi = v.get('rel_name_hi') or v.get('rel_name_en') or '-'
        phone = v.get('contact_phone') or '-'
        epic = v.get('epic_no') or '-'

        is_dup = bool(v.get('is_duplicate')) or (v.get('survey_status') == 'DUPLICATE')
        dup_pair_sl = v.get('duplicate_pair_sl')
        dup_pair_ward = v.get('duplicate_pair_ward')
        loc_str = f"वार्ड {dup_pair_ward} " if dup_pair_ward else ""

        if is_dup and dup_pair_sl:
            dup_badge = f'<span class="badge-dup">⚠️ डुप्लीकेट (मूल: {loc_str}क्र. #{dup_pair_sl})</span>'
        elif is_dup:
            dup_badge = '<span class="badge-dup">⚠️ डुप्लीकेट</span>'
        elif dup_pair_sl:
            dup_badge = f'<span class="badge-dup" style="background:#eff6ff; color:#1d4ed8; border-color:#93c5fd;">ℹ️ दोहराव (प्रतिलिपि: {loc_str}क्र. #{dup_pair_sl})</span>'
        else:
            dup_badge = ''
        row_class = ' class="dup-row"' if is_dup else ''

        rows_html.append(f"""
        <tr{row_class}>
          <td class="center bold">{v.get('sl_no') or idx}</td>
          <td><div class="bold">{n_hi} {dup_badge}</div>{n_en}</td>
          <td>{r_hi}</td>
          <td class="center font-mono">{epic}</td>
          <td class="center">{v.get('house_no') or '-'}</td>
          <td class="center">{v.get('age') or ''}/{v.get('gender') or ''}</td>
          <td class="center font-mono">{phone}</td>
          <td>{v.get('caste_tag') or '-'}</td>
        </tr>
        """)

    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<title>{ward_title} - मतदाता सूची ({report_type.upper()})</title>
<style>
  @page {{
    size: A4 portrait;
    margin: 8mm 6mm;
  }}
  * {{
    box-sizing: border-box;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}
  body {{
    font-family: 'Segoe UI', 'Nirmala UI', sans-serif;
    color: #0f172a;
    background: #fff;
    margin: 0;
  }}
  .header-card {{
    border-bottom: 2px solid #1e3a8a;
    padding-bottom: 4px;
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
  }}
  .title {{
    font-size: 13pt;
    font-weight: 900;
    color: #1e3a8a;
  }}
  .meta {{
    font-size: 8pt;
    color: #475569;
    font-weight: bold;
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
    font-weight: bold;
    padding: 4px 6px;
    border: 1px solid #0f172a;
    font-size: 7.5pt;
  }}
  td {{
    padding: 3.5px 5px;
    border: 1px solid #cbd5e1;
    vertical-align: middle;
  }}
  tr:nth-child(even) td {{
    background-color: #f8fafc;
  }}
  .center {{ text-align: center; }}
  .bold {{ font-weight: bold; color: #0f172a; }}
  .font-mono {{ font-family: monospace; font-size: 7.5pt; }}
  .badge-dup {{
    display: inline-block;
    font-size: 6.8pt;
    font-weight: 800;
    color: #991b1b;
    background: #fee2e2;
    border: 0.8px solid #f87171;
    border-radius: 3px;
    padding: 0.5px 4px;
    margin-left: 4px;
    vertical-align: middle;
  }}
  tr.dup-row td {{
    background-color: #fef2f2 !important;
  }}
  tr.dup-row td:first-child {{
    border-left: 3px solid #dc2626 !important;
  }}
</style>
</head>
<body>
<div class="header-card">
  <div>
    <div class="title">🗳️ {ward_title} — मतदाता सूची ({report_type.replace('_', ' ').title()})</div>
    <div class="meta">प्रत्याशी: {cand_name} | कुल रिकॉर्ड: {len(voters)} | मुद्रण तिथि: {datetime.datetime.now().strftime('%d-%m-%Y')}</div>
  </div>
</div>
<table>
  <thead>
    <tr>
      <th style="width:38px;">क्र.सं.</th>
      <th style="width:160px;">मतदाता नाम</th>
      <th style="width:140px;">संबंधी नाम</th>
      <th style="width:95px;">पहचान पत्र (EPIC)</th>
      <th style="width:45px;">मकान नं.</th>
      <th style="width:45px;">आयु/लिंग</th>
      <th style="width:90px;">मोबाइल नंबर</th>
      <th>समुदाय / जाति</th>
    </tr>
  </thead>
  <tbody>
    {''.join(rows_html)}
  </tbody>
</table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 4. API: Server-Side Vector PDF Download (Headless Chrome)
# ---------------------------------------------------------------------------

@reports_bp.route('/api/reports/export/pdf')
def api_reports_pdf():
    try:
        tenant, repo, tid, slug = _get_tenant_and_repo()
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

    html_content, status_code, _ = api_reports_print_html()
    if status_code != 200:
        return jsonify({'success': False, 'error': 'Failed to compile HTML'}), 500

    report_type = (request.args.get('type') or request.args.get('preset') or 'report').strip().lower()
    timestamp = int(datetime.datetime.now().timestamp())
    temp_dir = tempfile.gettempdir()
    temp_html_path = os.path.join(temp_dir, f"report_{slug}_{timestamp}.html")
    output_pdf_path = os.path.join(temp_dir, f"{slug.title()}_{report_type}_{timestamp}.pdf")

    try:
        with open(temp_html_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        # Detect Chrome / Chromium binary across Windows and Linux
        chrome_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
            "chrome"
        ]
        chrome_exe = "chrome"
        for candidate in chrome_candidates:
            if os.path.exists(candidate):
                chrome_exe = candidate
                break

        cmd = [
            chrome_exe,
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--print-to-pdf={output_pdf_path}",
            temp_html_path
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)

        if os.path.exists(temp_html_path):
            try:
                os.remove(temp_html_path)
            except Exception:
                pass

        if os.path.exists(output_pdf_path) and os.path.getsize(output_pdf_path) > 0:
            download_name = f"{slug.title()}_{report_type.title()}_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
            return send_file(output_pdf_path, as_attachment=True, download_name=download_name, mimetype='application/pdf')
        else:
            return jsonify({'success': False, 'error': f'Failed to render PDF: {res.stderr}'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# 5. Dedicated Interactive Counting Room Page (/reports/counting)
# ---------------------------------------------------------------------------

@reports_bp.route('/reports/counting')
def reports_counting_page():
    try:
        tenant, repo, tid, slug = _get_tenant_and_repo()
        cfg = load_tenant_config(tenant)
        from electionapp.services.counting_sheet_service import render_counting_sheet_html
        with repo.get_connection() as conn:
            html_output = render_counting_sheet_html(conn, tid, slug, cfg)
            return html_output, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f"<h1>Error loading counting sheet: {e}</h1>", 500
