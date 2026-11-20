"""
card_templates.py
HTML/CSS poster template builders for voter slips (Single, Family, Combined).
Encapsulates all layout markup, responsive sizing, typography, and bilingual styling.
"""

import os
from typing import Optional, List, Dict, Any, Tuple
from electionapp.config import load_tenant_config, is_panchayat_campaign


def _clean_booth_text(text: Any) -> str:
    """Sanitizes booth name or area text, auto-repairing or discarding any Mojibake."""
    if not text:
        return ''
    s = str(text).strip()
    if any(m in s for m in ('à¤', 'à¥', 'Ã', 'â', 'Â')):
        for enc in ('latin1', 'cp1252', 'iso-8859-1'):
            try:
                fixed = s.encode(enc).decode('utf-8')
                if not any(m in fixed for m in ('à¤', 'à¥', 'Ã')):
                    s = fixed
                    break
            except Exception:
                pass
        if any(m in s for m in ('à¤', 'à¥', 'Ã')):
            return ''
    try:
        from electionapp.translit.normalizer import strip_fffd_artifacts
        return strip_fffd_artifacts(s)
    except Exception:
        return s


def _resolve_booth_details(voter: Dict[str, Any], booth: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    """
    Resolves booth name and area with priority:
    1. Voter row's own booth_name / booth_area if present and clean.
    2. Fallback to booth config lookup (booth['name'] / booth['area']).
    3. Guarantees zero Mojibake leakage.
    """
    booth = booth or {}
    v_bname = _clean_booth_text(voter.get('booth_name'))
    cfg_bname = _clean_booth_text(booth.get('name'))
    booth_name = v_bname or cfg_bname

    v_barea = _clean_booth_text(voter.get('booth_area'))
    cfg_barea = _clean_booth_text(booth.get('area'))
    booth_area = v_barea or cfg_barea

    return booth_name, booth_area


from electionapp.translit.normalizer import clean_devnagari_name, clean_english_name


def _asset_src(branding: Dict[str, Any], key: str) -> str:
    """Returns the image source for a slip asset, preferring a file:// URL.

    Chrome cannot cache a base64 data: URI, so inlining the assets forced a
    full PNG re-decode on every render (the candidate photo alone is 1.4MB,
    inflating the HTML to ~2.1MB). A file:// URL lets Blink's MemoryCache
    hold the decoded bitmap across renders. Both the CDP daemons and the CLI
    fallback already run with --allow-file-access-from-files.

    Set SLIP_ASSET_MODE=data to force the legacy inlined behaviour.
    """
    if os.environ.get('SLIP_ASSET_MODE', 'file').strip().lower() != 'data':
        url = branding.get(f'{key}_url')
        if url:
            return url
    return branding.get(f'{key}_src') or ""


def format_bilingual(name_en: Optional[str], name_hi: Optional[str]) -> str:
    """Formats English and Hindi text with bilingual fallback."""
    hi = clean_devnagari_name(name_hi)
    en = clean_english_name(name_en, fallback_hi=hi)
    if en and hi and en.lower() != hi.lower():
        return f"{en} / {hi}"
    return en or hi


def build_custom_bg_single_html(
    voter: Dict[str, Any],
    booth: Dict[str, Any],
    branding: Dict[str, Any],
    custom_bg_src: str,
    tenant: str = 'ward82'
) -> str:
    """Builds HTML for a single-voter slip rendered on a custom graphic background."""
    v = voter
    booth_name, booth_area = _resolve_booth_details(v, booth)
    name_disp = format_bilingual(v.get('name_en', ''), v.get('name_hi', ''))
    rel_disp = format_bilingual(v.get('rel_name_en', ''), v.get('rel_name_hi', ''))
    gender_disp = "पुरुष" if v.get('gender') in ['पुरुष', 'Male'] else "महिला"

    w_no = v.get('ward_no')
    cfg = branding.get('cfg')
    is_panchayat = is_panchayat_campaign(cfg) if cfg else False
    has_explicit_ward = bool(is_panchayat and w_no and str(w_no).isdigit() and int(w_no) > 0)
    p_no = v.get('part_no') or 1

    if has_explicit_ward:
        cb_badge_grid_html = f"""
      <div class="badge-box">
        <div class="badge-label">वार्ड संख्या (WARD NO.)</div>
        <div class="badge-num">{w_no}</div>
      </div>
      <div class="badge-box" style="border-color: #1e40af; background: #eff6ff;">
        <div class="badge-label">भाग संख्या (PART NO.)</div>
        <div class="badge-num">{p_no}</div>
      </div>
      <div class="badge-box sl">
        <div class="badge-label">क्रम संख्या (SERIAL NO.)</div>
        <div class="badge-num">{v.get('sl_no')}</div>
      </div>"""
    else:
        cb_badge_grid_html = f"""
      <div class="badge-box">
        <div class="badge-label">भाग संख्या (PART NO.)</div>
        <div class="badge-num">{p_no}</div>
      </div>
      <div class="badge-box sl">
        <div class="badge-label">क्रम संख्या (SERIAL NO.)</div>
        <div class="badge-num">{v.get('sl_no')}</div>
      </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 1080px;
      height: 1440px;
      background: #f8fafc url('{custom_bg_src}') no-repeat top left;
      background-size: 1080px 1440px;
      font-family: 'Segoe UI', Arial, sans-serif;
      position: relative;
      overflow: hidden;
    }}
    .data-box {{
      position: absolute;
      top: 350px;
      left: 40px;
      width: 1000px;
      height: 880px;
      background: #ffffff;
      border: 3px solid #ea580c;
      border-radius: 20px;
      padding: 24px 30px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.08);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .badge-grid {{
      display: flex;
      gap: 20px;
      margin-bottom: 14px;
    }}
    .badge-box {{
      flex: 1;
      border: 3px solid #1e40af;
      border-radius: 12px;
      padding: 10px 16px;
      text-align: center;
      background: #eff6ff;
    }}
    .badge-box.sl {{
      border-color: #ea580c;
      background: #fff7ed;
    }}
    .badge-label {{
      font-size: 17px;
      font-weight: 800;
      color: #475569;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .badge-num {{
      font-size: 52px;
      font-weight: 900;
      color: #1e3a8a;
      line-height: 1.1;
      margin-top: 2px;
    }}
    .badge-box.sl .badge-num {{
      color: #ea580c;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 14px;
      background: #ffffff;
      border-radius: 12px;
      border: 2px solid #e2e8f0;
      overflow: hidden;
    }}
    td {{
      padding: 13px 18px;
      border-bottom: 1.5px dashed #cbd5e1;
      font-size: 23px;
      vertical-align: middle;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    td.lbl {{
      width: 35%;
      color: #64748b;
      font-weight: 700;
    }}
    td.data {{
      width: 65%;
      font-weight: 800;
      color: #0f172a;
    }}
    .booth-box {{
      background: #f8fafc;
      border: 2.5px solid #cbd5e1;
      border-left: 7px solid #ea580c;
      border-radius: 12px;
      padding: 14px 20px;
    }}
    .booth-title {{
      color: #ea580c;
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 4px;
    }}
    .booth-name {{
      font-weight: 800;
      font-size: 22px;
      color: #0f172a;
      line-height: 1.3;
    }}
  </style>
</head>
<body>
  <div class="data-box">
    <div class="badge-grid">
      {cb_badge_grid_html}
    </div>

    <table>
      <tr><td class="lbl">मतदाता का नाम:</td><td class="data">{name_disp}</td></tr>
      <tr><td class="lbl">{v.get('rel_type', 'पिता/पति')}:</td><td class="data">{rel_disp}</td></tr>
      <tr><td class="lbl">मकान संख्या:</td><td class="data">{v.get('house_no') or '-'}</td></tr>
      <tr><td class="lbl">आयु / लिंग:</td><td class="data">{f"{v.get('age')} वर्ष | {gender_disp}" if (v.get('age') and str(v.get('age')).lower() != 'none') else gender_disp}</td></tr>
      <tr><td class="lbl">पहचान पत्र (EPIC):</td><td class="data" style="font-family: monospace; color: #1e40af; font-size: 24px;">{v.get('epic_no') or 'उपलब्ध नहीं'}</td></tr>
    </table>

    <div class="booth-box">
      <div class="booth-title">📍 मतदान केंद्र (Polling Station):</div>
      <div class="booth-name">{booth_name}</div>
    </div>
  </div>
</body>
</html>"""


def build_custom_bg_family_html(
    page_voters: List[Dict[str, Any]],
    all_voters: List[Dict[str, Any]],
    branding: Dict[str, Any],
    custom_bg_src: str,
    page: int,
    total_pages: int,
    slip_type: str = 'Family'
) -> str:
    """Builds HTML for a paginated family/multi-voter slip on custom graphic background."""
    start_idx = (page - 1) * len(page_voters)
    cfg = branding.get('cfg')
    is_panchayat = is_panchayat_campaign(cfg) if cfg else False
    has_any_ward = bool(is_panchayat and any(bool(v.get('ward_no') and str(v.get('ward_no')).isdigit() and int(v.get('ward_no')) > 0) for v in (all_voters or page_voters)))
    multi_part_col_title = "वार्ड / भाग" if has_any_ward else "भाग"
    rows_html = ""
    for idx, v in enumerate(page_voters, start_idx + 1):
        name_d = format_bilingual(v.get('name_en', ''), v.get('name_hi', ''))
        rel_d = format_bilingual(v.get('rel_name_en', ''), v.get('rel_name_hi', ''))
        gender_short = "M" if v.get('gender') in ['पुरुष', 'Male'] else "F"
        w_val = v.get('ward_no')
        has_w = bool(is_panchayat and w_val and str(w_val).isdigit() and int(w_val) > 0)
        p_val = v.get('part_no') or ''
        if has_w and p_val:
            part_disp = f"W.{w_val} / P.{p_val}"
            part_font = "14px"
        elif has_w:
            part_disp = f"W.{w_val}"
            part_font = "18px"
        else:
            part_disp = f"P.{p_val}"
            part_font = "18px"
        rows_html += f"""
        <tr>
          <td style="text-align:center;font-weight:bold;font-size:18px;">{idx}</td>
          <td style="text-align:center;font-weight:bold;font-size:{part_font};">{part_disp}</td>
          <td style="text-align:center;font-weight:900;font-size:22px;color:#ea580c;">{v.get('sl_no')}</td>
          <td style="font-size:19px;"><strong>{name_d}</strong><br><small style="color:#64748b;font-size:16px;">{v.get('rel_type', 'Rel')}: {rel_d}</small></td>
          <td style="text-align:center;font-weight:bold;font-size:18px;">{v.get('house_no') or '-'}</td>
          <td style="text-align:center;font-size:18px;">{f"{v.get('age')} Y / {gender_short}" if (v.get('age') and str(v.get('age')).lower() != 'none') else gender_short}</td>
          <td style="font-family:monospace;font-size:18px;color:#1e40af;font-weight:700;">{v.get('epic_no') or '-'}</td>
        </tr>"""

    unique_parts = sorted(list(set(v.get('part_no') for v in page_voters)))
    if len(unique_parts) == 1:
        # 1. Authoritative truth from voter database record
        b_name = ''
        for pv in page_voters:
            pv_name = _clean_booth_text(pv.get('booth_name'))
            if pv_name:
                b_name = pv_name
                break
        # 2. Fallback to campaign config if database column was empty
        if not b_name:
            b = branding['get_booth'](unique_parts[0])
            b_name = _clean_booth_text(b.get('name', ''))
        booth_html = f"""
        <div class="booth-box">
          <div class="booth-title">📍 मतदान केंद्र (Polling Station - भाग संख्या {unique_parts[0]}):</div>
          <div class="booth-name">{b_name}</div>
        </div>"""
    else:
        booth_rows = []
        for p in unique_parts:
            # 1. Authoritative truth from voter database record
            p_name = ''
            for pv in page_voters:
                if pv.get('part_no') == p:
                    pv_name = _clean_booth_text(pv.get('booth_name'))
                    if pv_name:
                        p_name = pv_name
                        break
            # 2. Fallback to campaign config if database column was empty
            if not p_name:
                p_booth = branding['get_booth'](p)
                p_name = _clean_booth_text(p_booth.get("name", ""))
            booth_rows.append(f'<div><strong>भाग {p}:</strong> {p_name}</div>')
        booths_joined = "".join(booth_rows)
        booth_html = f"""
        <div class="booth-box">
          <div class="booth-title">📍 मतदान केंद्र विवरण:</div>
          <div style="font-size: 16px; margin-top: 4px; color: #0f172a;">{booths_joined}</div>
        </div>"""

    voters_to_check = all_voters or page_voters
    houses = set(str(v.get('house_no', '')).strip() for v in voters_to_check if str(v.get('house_no', '')).strip() not in ('', '0', '-'))
    parts = set(str(v.get('part_no', '')).strip() for v in voters_to_check if str(v.get('part_no', '')).strip())
    if slip_type == 'Combined' or len(houses) > 1 or len(parts) > 1:
        title = "संयुक्त मतदाता पर्ची"
    else:
        title = "परिवार मतदाता पर्ची"
    if total_pages > 1:
        header_title = f"{title} (भाग {page}/{total_pages})"
        family_count_str = f"भाग {page}/{total_pages} · {len(page_voters)}/{len(all_voters)} सदस्य"
    else:
        header_title = title
        family_count_str = f"{len(all_voters)} सदस्य (Members)"

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 1080px;
      height: 1440px;
      background: #f8fafc url('{custom_bg_src}') no-repeat top left;
      background-size: 1080px 1440px;
      font-family: 'Segoe UI', Arial, sans-serif;
      position: relative;
      overflow: hidden;
    }}
    .data-box {{
      position: absolute;
      top: 350px;
      left: 40px;
      width: 1000px;
      height: 880px;
      background: #ffffff;
      border: 3px solid #ea580c;
      border-radius: 18px;
      padding: 18px 22px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.08);
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }}
    .family-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 2.5px solid #ea580c;
      padding-bottom: 8px;
      margin-bottom: 10px;
    }}
    .family-title {{
      font-size: 26px;
      font-weight: 900;
      color: #1e3a8a;
    }}
    .family-count {{
      font-size: 18px;
      font-weight: 800;
      color: #ea580c;
      background: #fff7ed;
      border: 1.5px solid #ea580c;
      padding: 4px 14px;
      border-radius: 20px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 10px;
      background: #ffffff;
      border-radius: 10px;
      border: 1.5px solid #cbd5e1;
      overflow: hidden;
    }}
    th {{
      background: #f1f5f9;
      color: #1e3a8a;
      padding: 10px 6px;
      border: 1.5px solid #cbd5e1;
      font-size: 16px;
      text-transform: uppercase;
      text-align: center;
      font-weight: 800;
    }}
    td {{
      padding: 8px 8px;
      border: 1.5px solid #cbd5e1;
      vertical-align: middle;
      color: #0f172a;
    }}
    .booth-box {{
      background: #f8fafc;
      border: 2px solid #cbd5e1;
      border-left: 6px solid #ea580c;
      border-radius: 10px;
      padding: 12px 18px;
    }}
    .booth-title {{
      color: #ea580c;
      font-size: 16px;
      font-weight: 800;
      margin-bottom: 2px;
    }}
    .booth-name {{
      font-weight: 800;
      font-size: 20px;
      color: #0f172a;
      line-height: 1.3;
    }}
  </style>
</head>
<body>
  <div class="data-box">
    <div class="family-header">
      <div class="family-title">👨‍👩‍👧‍👦 {header_title}</div>
      <div class="family-count">{family_count_str}</div>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width:45px;">क्र.</th>
          <th style="width:75px;">{multi_part_col_title}</th>
          <th style="width:75px;color:#ea580c;">क्रम सं.</th>
          <th>मतदाता का नाम व संबंध</th>
          <th style="width:75px;">मकान</th>
          <th style="width:85px;">आयु/लिंग</th>
          <th style="width:145px;">पहचान पत्र (EPIC)</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    {booth_html}
  </div>
</body>
</html>"""


def build_standard_single_html(
    voter: Dict[str, Any],
    booth: Dict[str, Any],
    branding: Dict[str, Any],
    tenant: str = 'ward82'
) -> str:
    """Builds HTML for the standard single voter card with candidate branding, symbols, and QR/ballot details."""
    v = voter
    booth_name, booth_area = _resolve_booth_details(v, booth)
    name_disp = format_bilingual(v.get('name_en', ''), v.get('name_hi', ''))
    rel_disp = format_bilingual(v.get('rel_name_en', ''), v.get('rel_name_hi', ''))
    gender_disp = "पुरुष" if v.get('gender') in ['पुरुष', 'Male'] else "महिला"

    lotus_src = _asset_src(branding, 'lotus')
    candidate_src = _asset_src(branding, 'candidate')
    has_symbol = branding['has_symbol']
    has_candidate = branding['has_candidate']
    has_ballot = branding['has_ballot']
    ballot_no = branding['ballot_no']
    symbol_name_hi = branding['symbol_name_hi']
    party_line = branding['party_line']
    candidate_disp = branding['candidate_disp']
    place_hi = branding['place_hi']
    appeal_msg = branding['appeal_msg']
    slogan = branding['slogan']
    theme_color = branding['theme_color']

    w_no = v.get('ward_no')
    cfg = branding.get('cfg') or load_tenant_config(tenant)
    is_panchayat = is_panchayat_campaign(cfg)
    has_explicit_ward = bool(is_panchayat and w_no and str(w_no).isdigit() and int(w_no) > 0)
    p_no = str(v.get('part_no') or v.get('part') or '')

    if has_explicit_ward:
        badge_grid_html = f"""
      <div class="badge-box" style="border-color: #4f46e5; background: #eef2ff;">
        <div class="badge-label" style="color: #4338ca;">वार्ड संख्या (WARD)</div>
        <div class="badge-num" style="color: #3730a3; font-size: 38px;">{w_no}</div>
      </div>
      <div class="badge-box" style="border-color: #1e40af; background: #eff6ff;">
        <div class="badge-label" style="color: #1e3a8a;">भाग संख्या (PART)</div>
        <div class="badge-num" style="color: #1e3a8a; font-size: 38px;">{p_no}</div>
      </div>
      <div class="badge-box sl">
        <div class="badge-label" style="color: #c2410c;">क्रम संख्या (SERIAL)</div>
        <div class="badge-num" style="font-size: 38px;">{v.get('sl_no')}</div>
      </div>"""
    else:
        badge_grid_html = f"""
      <div class="badge-box">
        <div class="badge-label">भाग संख्या (PART)</div>
        <div class="badge-num">{p_no}</div>
      </div>
      <div class="badge-box sl">
        <div class="badge-label">क्रम संख्या (SERIAL)</div>
        <div class="badge-num">{v.get('sl_no')}</div>
      </div>"""

    ballot_tag_html = f'<div class="ballot-tag">क्र. सं. <span>{ballot_no}</span></div>' if ballot_no else '<div class="ballot-tag symbol-top-tag">चुनाव चिन्ह</div>'
    cand_title = branding.get('cand_title') or party_line
    party_title_text = cand_title if (has_symbol and cand_title) else party_line

    if has_symbol and has_candidate:
        header_html = f"""
    <div class="header-row">
      <div class="symbol-badge">
        {ballot_tag_html}
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="symbol-label">{symbol_name_hi}</div>
      </div>
      <div class="header-text">
        <div class="party-title">{party_title_text}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">मतदाता सूचना पर्ची (Voter Slip) · {place_hi}</div>
      </div>
      <div class="candidate-badge">
        <img src="{candidate_src}" alt="{candidate_disp}">
      </div>
    </div>"""
    elif has_candidate and candidate_src:
        header_html = f"""
    <div class="header-row">
      <div class="header-text" style="text-align: left;">
        <div class="party-title">{party_line}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">मतदाता सूचना पर्ची (Voter Slip) · {place_hi}</div>
      </div>
      <div class="candidate-badge">
        <img src="{candidate_src}" alt="{candidate_disp}">
      </div>
    </div>"""
    elif has_symbol and lotus_src:
        header_html = f"""
    <div class="header-row">
      <div class="symbol-badge">
        {ballot_tag_html}
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="symbol-label">{symbol_name_hi}</div>
      </div>
      <div class="header-text" style="text-align: left;">
        <div class="party-title">{party_title_text}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">मतदाता सूचना पर्ची (Voter Slip) · {place_hi}</div>
      </div>
    </div>"""
    elif has_candidate or has_symbol:
        header_html = f"""
    <div class="header-row" style="text-align: center; display: block; border-bottom: 2px solid #bfdbfe; padding-bottom: 10px; margin-bottom: 12px;">
      {f'<div style="font-size: 13px; font-weight: 800; color: {theme_color}; text-transform: uppercase; letter-spacing: 0.6px;">{party_line}</div>' if party_line else ''}
      {f'<div style="font-size: 26px; font-weight: 900; color: #1e3a8a; margin: 3px 0 2px; line-height: 1.2;">{candidate_disp}</div>' if candidate_disp else ''}
      <div style="font-size: 13px; font-weight: 700; color: #475569;">मतदाता सूचना पर्ची (Voter Slip) · {place_hi}</div>
    </div>"""
    else:
        header_html = f"""
    <div class="header-row" style="text-align: center; display: block; border-bottom: 2px solid #bfdbfe; padding-bottom: 8px; margin-bottom: 12px;">
      <div style="font-size: 11px; font-weight: 800; color: {theme_color}; text-transform: uppercase; letter-spacing: 0.8px;">
        डिजिटल मतदाता सेवा व चुनाव प्रबंधन प्रणाली
      </div>
      <div style="font-size: 23px; font-weight: 900; color: #1e3a8a; margin: 3px 0 2px; line-height: 1.2;">
        मतदाता सूचना पर्ची (Voter Slip)
      </div>
      <div style="font-size: 13px; font-weight: 700; color: #475569;">
        {place_hi}
      </div>
    </div>"""

    is_ballot_election = bool(branding.get('is_ballot_election') or branding.get('voting_method') == 'BALLOT_PAPER')
    ballot_color = str(branding.get('ballot_color') or '').strip()
    ballot_color_badge = (
        f'<span class="ballot-color-pill" style="display:inline-block; margin-left:8px; padding:2px 8px; font-size:11px; font-weight:bold; border-radius:12px; background:#f1f5f9; color:#334155; border:1px solid #cbd5e1;">मतपत्र रंग: {ballot_color}</span>'
        if ballot_color else ''
    )

    if is_ballot_election:
        banner_title_html = f'<div class="ballot-banner-title">मतपत्र क्रम संख्या: {ballot_no}{ballot_color_badge}</div>'
        voter_guidance_text = "🙏 कृपया पहचान पत्र साथ लाएं · मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें"
    else:
        banner_title_html = f'<div class="ballot-banner-title">ईवीएम क्रम संख्या: {ballot_no}{ballot_color_badge}</div>'
        voter_guidance_text = "🙏 कृपया मतदान के दिन अपना पहचान पत्र (Voter ID / आधार कार्ड) साथ लाएं।"

    if has_ballot and has_symbol:
        ballot_html = f"""
    <div class="ballot-banner">
      <div class="ballot-banner-left">
        <div class="ballot-big-num">{ballot_no}</div>
        <div class="ballot-banner-txt">
          {banner_title_html}
          <div class="ballot-banner-sub">{appeal_msg}</div>
        </div>
      </div>
      <div class="ballot-banner-symbol">
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="ballot-banner-symbol-txt">{symbol_name_hi}</div>
      </div>
    </div>"""
    else:
        ballot_html = ""

    if has_ballot and has_symbol:
        footer_html = f"""
    <div class="footer-bar">
      <div class="slogan">🗳️ {slogan} 🗳️</div>
      <div class="footer-note">{voter_guidance_text}</div>
    </div>"""
    else:
        footer_guidance = voter_guidance_text if is_ballot_election else "🙏 कृपया मतदान दिवस पर अपना पहचान पत्र (Voter ID / आधार कार्ड) साथ लाएं।"
        footer_html = f"""
    <div class="footer-bar" style="border-top: 1.5px solid #bfdbfe; padding-top: 8px;">
      <div class="slogan" style="color: {theme_color}; font-size: 14px; font-weight: 900; margin-bottom: 3px;">🗳️ {slogan} 🗳️</div>
      <div class="footer-note">{footer_guidance}</div>
    </div>"""

    area_markup = f'<div class="booth-area">क्षेत्र: {booth_area}</div>' if booth_area else ''

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      font-family: 'Segoe UI', Arial, sans-serif;
      overflow: hidden;
      width: 610px;
    }}
    ::-webkit-scrollbar {{ display: none !important; }}
    .card {{
      width: 610px;
      margin: 0;
      background: #ffffff;
      border: 3.5px solid {theme_color};
      border-radius: 14px;
      padding: 14px 16px;
      position: relative;
    }}
    .top-strip {{
      height: 6px;
      background: linear-gradient(90deg, #ff9933 0%, #ffffff 50%, #138808 100%);
      border-radius: 3px;
      margin-bottom: 12px;
    }}
    .header-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-bottom: 12px;
      border-bottom: 2px solid #bfdbfe;
      margin-bottom: 12px;
    }}
    .symbol-badge {{
      width: 115px;
      height: 138px;
      border: 2.5px solid {theme_color};
      border-radius: 12px;
      background: #ffffff;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 4px 10px rgba(37, 99, 235, 0.18);
      padding: 5px 6px;
      flex-shrink: 0;
      position: relative;
      box-sizing: border-box;
    }}
    .ballot-tag {{
      width: 100%;
      background: {theme_color};
      color: #ffffff;
      text-align: center;
      font-size: 12.5px;
      font-weight: 900;
      padding: 3px 0;
      border-radius: 6px;
      letter-spacing: 0.5px;
      line-height: 1.2;
    }}
    .ballot-tag.symbol-top-tag {{
      font-size: 11.5px;
      font-weight: 800;
      letter-spacing: 0.6px;
      padding: 3px 0;
    }}
    .ballot-tag span {{
      background: #ffffff;
      color: {theme_color};
      padding: 1px 7px;
      border-radius: 4px;
      font-size: 14.5px;
      font-weight: 900;
      margin-left: 3px;
    }}
    .symbol-badge img {{
      width: 76px;
      height: 76px;
      max-width: 82px;
      max-height: 78px;
      object-fit: contain;
      margin: auto 0;
      display: block;
    }}
    .symbol-label {{
      font-size: 11px;
      font-weight: 800;
      color: {theme_color};
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      border-radius: 5px;
      padding: 2px 6px;
      text-align: center;
      width: 100%;
      box-sizing: border-box;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .candidate-badge {{
      width: 115px;
      height: 138px;
      border: 2.5px solid {theme_color};
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 10px rgba(0,0,0,0.15);
      position: relative;
      flex-shrink: 0;
      background: linear-gradient(180deg, #ffffff 0%, #eff6ff 100%);
      display: flex;
      align-items: flex-end;
      justify-content: center;
    }}
    .candidate-badge img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .header-text {{
      flex: 1;
      text-align: center;
    }}
    .party-title {{
      font-size: 12.5px;
      font-weight: 800;
      color: {theme_color};
      letter-spacing: 0.5px;
      text-transform: uppercase;
      margin: 0;
    }}
    .candidate-name {{
      font-size: 24px;
      font-weight: 900;
      color: #1e3a8a;
      margin: 4px 0 2px;
      line-height: 1.2;
    }}
    .ward-subtitle {{
      font-size: 12px;
      font-weight: 700;
      color: #475569;
      margin: 0;
    }}
    .badge-grid {{
      display: flex;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .badge-box {{
      flex: 1;
      border: 2.5px solid #1e40af;
      border-radius: 10px;
      padding: 6px 10px;
      text-align: center;
      background: #eff6ff;
    }}
    .badge-box.sl {{
      border-color: {theme_color};
      background: #fff7ed;
    }}
    .badge-label {{
      font-size: 12px;
      font-weight: 800;
      color: #475569;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge-num {{
      font-size: 44px;
      font-weight: 900;
      color: #1e3a8a;
      line-height: 1.1;
      margin-top: 2px;
    }}
    .badge-box.sl .badge-num {{
      color: {theme_color};
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 12px;
      background: #ffffff;
      border-radius: 8px;
      border: 1.5px solid #e2e8f0;
      overflow: hidden;
    }}
    td {{
      padding: 7px 12px;
      border-bottom: 1px solid #f1f5f9;
      font-size: 14.5px;
      vertical-align: middle;
    }}
    tr:last-child td {{
      border-bottom: none;
    }}
    td.lbl {{
      width: 32%;
      color: #64748b;
      font-weight: 700;
    }}
    td.data {{
      width: 68%;
      font-weight: 800;
      color: #0f172a;
    }}
    .booth-box {{
      background: #ffffff;
      border: 1.5px solid #bfdbfe;
      border-left: 5px solid {theme_color};
      border-radius: 8px;
      padding: 8px 12px;
      margin-bottom: 12px;
    }}
    .booth-label {{
      color: {theme_color};
      font-weight: 800;
      font-size: 12px;
    }}
    .booth-name {{
      font-weight: 800;
      font-size: 13.5px;
      color: #0f172a;
      margin-top: 2px;
      line-height: 1.35;
    }}
    .booth-area {{
      color: #64748b;
      font-size: 12px;
      margin-top: 3px;
    }}
    .ballot-banner {{
      background: linear-gradient(135deg, {theme_color} 0%, #1e40af 100%);
      border-radius: 10px;
      padding: 8px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #ffffff;
      margin-bottom: 12px;
      box-shadow: 0 4px 10px rgba(37, 99, 235, 0.25);
    }}
    .ballot-banner-left {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .ballot-big-num {{
      font-size: 32px;
      font-weight: 900;
      background: #ffffff;
      color: {theme_color};
      width: 44px;
      height: 44px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
      box-shadow: 0 2px 5px rgba(0,0,0,0.2);
    }}
    .ballot-banner-txt {{
      display: flex;
      flex-direction: column;
    }}
    .ballot-banner-title {{
      font-size: 16px;
      font-weight: 900;
      letter-spacing: 0.3px;
    }}
    .ballot-banner-sub {{
      font-size: 12px;
      font-weight: 700;
      opacity: 0.95;
    }}
    .ballot-banner-symbol {{
      display: flex;
      align-items: center;
      gap: 8px;
      background: #ffffff;
      border-radius: 8px;
      padding: 5px 12px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.15);
    }}
    .ballot-banner-symbol img {{
      width: 32px;
      height: 32px;
      object-fit: contain;
    }}
    .ballot-banner-symbol-txt {{
      font-size: 14px;
      font-weight: 900;
      color: {theme_color};
    }}
    .footer-bar {{
      text-align: center;
      border-top: 1.5px solid #bfdbfe;
      padding-top: 8px;
    }}
    .slogan {{
      font-size: 14px;
      font-weight: 900;
      color: {theme_color};
      margin-bottom: 2px;
    }}
    .footer-note {{
      font-size: 11px;
      color: #64748b;
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="top-strip"></div>
    {header_html}
    {ballot_html}

    <div class="badge-grid">
      {badge_grid_html}
    </div>

    <table>
      <tr><td class="lbl">मतदाता का नाम:</td><td class="data">{name_disp}</td></tr>
      <tr><td class="lbl">{v.get('rel_type', 'पिता/पति')}:</td><td class="data">{rel_disp}</td></tr>
      <tr><td class="lbl">मकान संख्या:</td><td class="data">{v.get('house_no') or '-'}</td></tr>
      <tr><td class="lbl">आयु / लिंग:</td><td class="data">{f"{v.get('age')} वर्ष | {gender_disp}" if (v.get('age') and str(v.get('age')).lower() != 'none') else gender_disp}</td></tr>
      <tr><td class="lbl">पहचान पत्र (EPIC):</td><td class="data" style="font-family: monospace; color: #1e40af; font-size: 15.5px;">{v.get('epic_no') or 'उपलब्ध नहीं'}</td></tr>
    </table>

    <div class="booth-box">
      <div class="booth-label">📍 मतदान केंद्र (Polling Station):</div>
      <div class="booth-name">{booth_name}</div>
      {area_markup}
    </div>

    {footer_html}
  </div>
</body>
</html>"""


def build_standard_family_html(
    page_voters: List[Dict[str, Any]],
    branding: Dict[str, Any],
    page: int,
    total_pages: int,
    slip_type: str = 'Family',
    tenant: str = 'ward82',
    all_voters: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Builds HTML for standard multi-voter / family table card."""
    lotus_src = _asset_src(branding, 'lotus')
    candidate_src = _asset_src(branding, 'candidate')
    has_symbol = branding['has_symbol']
    has_candidate = branding['has_candidate']
    has_ballot = branding['has_ballot']
    ballot_no = branding['ballot_no']
    symbol_name_hi = branding['symbol_name_hi']
    party_line = branding['party_line']
    candidate_disp = branding['candidate_disp']
    place_hi = branding['place_hi']
    appeal_msg = branding['appeal_msg']
    slogan = branding['slogan']
    theme_color = branding['theme_color']

    cfg = branding.get('cfg') or load_tenant_config(tenant)
    is_panchayat = is_panchayat_campaign(cfg)
    has_any_ward = bool(is_panchayat and any(bool(v.get('ward_no') and str(v.get('ward_no')).isdigit() and int(v.get('ward_no')) > 0) for v in page_voters))
    multi_part_col_title = "वार्ड / भाग" if has_any_ward else "भाग सं."

    def_kramank_font = "18px"
    def_th_pad = "6px 4px"
    def_td_pad = "4px 6px"

    rows_html = ""
    for idx, v in enumerate(page_voters, 1):
        name_d = format_bilingual(v.get('name_en', ''), v.get('name_hi', ''))
        rel_d = format_bilingual(v.get('rel_name_en', ''), v.get('rel_name_hi', ''))
        gender_short = "पु." if v.get('gender') in ['पुरुष', 'Male'] else "म."
        w_val = v.get('ward_no')
        has_w = bool(is_panchayat and w_val and str(w_val).isdigit() and int(w_val) > 0)
        p_val = v.get('part_no') or ''
        if has_w and p_val:
            part_disp = f"W.{w_val} / P.{p_val}"
            part_font = "11px"
        elif has_w:
            part_disp = f"W.{w_val}"
            part_font = "12px"
        else:
            part_disp = f"P.{p_val}"
            part_font = "12px"
        rows_html += f"""
        <tr>
          <td style="text-align:center;font-weight:bold;font-size:{part_font};line-height:1.2;">{part_disp}</td>
          <td class="kramank-cell">{v.get('sl_no')}</td>
          <td style="font-size:13px;line-height:1.25;"><strong>{name_d}</strong><br><small style="color:#64748b;font-size:11px;">{v.get('rel_type', 'Rel')}: {rel_d}</small></td>
          <td style="text-align:center;font-size:12px;">{v.get('house_no') or '-'}</td>
          <td style="text-align:center;font-size:12px;">{f"{v.get('age')} / {gender_short}" if (v.get('age') and str(v.get('age')).lower() != 'none') else gender_short}</td>
          <td style="font-family:monospace;font-size:11.5px;color:#1e40af;font-weight:700;">{v.get('epic_no') or '-'}</td>
        </tr>"""

    unique_parts = sorted(list(set(v.get('part_no') for v in page_voters)))
    if len(unique_parts) == 1:
        # 1. Authoritative truth from voter database record
        b_name = ''
        for pv in page_voters:
            pv_name = _clean_booth_text(pv.get('booth_name'))
            if pv_name:
                b_name = pv_name
                break
        # 2. Fallback to campaign config if database column was empty
        if not b_name:
            b = branding['get_booth'](unique_parts[0])
            b_name = _clean_booth_text(b.get('name', ''))
        booth_html = f"""
        <div class="booth-box">
          <div class="booth-label">📍 मतदान केंद्र (Polling Station - भाग संख्या {unique_parts[0]}):</div>
          <div class="booth-name">{b_name}</div>
        </div>"""
    else:
        booth_rows = []
        for p in unique_parts:
            p_booth = branding['get_booth'](p)
            p_name = p_booth.get("name", "")
            if not p_name:
                for pv in page_voters:
                    if pv.get('part_no') == p and pv.get('booth_name'):
                        p_name = pv.get('booth_name')
                        break
            booth_rows.append(f'<div><strong>भाग {p}:</strong> {p_name}</div>')
        booth_html = f"""
        <div class="booth-box">
          <div class="booth-label">📍 मतदान केंद्र विवरण:</div>
          <div class="booth-name" style="font-size: 11.5px; font-weight: normal; margin-top: 4px;">{"".join(booth_rows)}</div>
        </div>"""

    voters_to_check = all_voters or page_voters
    houses = set(str(v.get('house_no', '')).strip() for v in voters_to_check if str(v.get('house_no', '')).strip() not in ('', '0', '-'))
    parts = set(str(v.get('part_no', '')).strip() for v in voters_to_check if str(v.get('part_no', '')).strip())
    if slip_type == 'Combined' or len(houses) > 1 or len(parts) > 1:
        title = "संयुक्त मतदाता पर्ची"
    else:
        title = "परिवार मतदाता पर्ची"
    page_suffix = f" (भाग {page}/{total_pages})" if total_pages > 1 else ""

    ballot_tag_html = f'<div class="ballot-tag">क्र. सं. <span>{ballot_no}</span></div>' if ballot_no else '<div class="ballot-tag symbol-top-tag">चुनाव चिन्ह</div>'
    cand_title = branding.get('cand_title') or party_line
    party_title_text = cand_title if (has_symbol and cand_title) else party_line

    if has_symbol and has_candidate:
        multi_header_html = f"""
    <div class="header-row">
      <div class="symbol-badge">
        {ballot_tag_html}
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="symbol-label">{symbol_name_hi}</div>
      </div>
      <div class="header-text">
        <div class="party-title">{party_title_text}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">{title}{page_suffix} · {place_hi}</div>
      </div>
      <div class="candidate-badge">
        <img src="{candidate_src}" alt="{candidate_disp}">
      </div>
    </div>"""
    elif has_candidate and candidate_src:
        multi_header_html = f"""
    <div class="header-row">
      <div class="header-text" style="text-align: left;">
        <div class="party-title">{party_line}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">{title}{page_suffix} · {place_hi}</div>
      </div>
      <div class="candidate-badge">
        <img src="{candidate_src}" alt="{candidate_disp}">
      </div>
    </div>"""
    elif has_symbol and lotus_src:
        multi_header_html = f"""
    <div class="header-row">
      <div class="symbol-badge">
        {ballot_tag_html}
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="symbol-label">{symbol_name_hi}</div>
      </div>
      <div class="header-text" style="text-align: left;">
        <div class="party-title">{party_title_text}</div>
        <div class="candidate-name">{candidate_disp}</div>
        <div class="ward-subtitle">{title}{page_suffix} · {place_hi}</div>
      </div>
    </div>"""
    elif has_candidate or has_symbol:
        multi_header_html = f"""
    <div class="header-row" style="text-align: center; display: block; border-bottom: 2px solid #bfdbfe; padding-bottom: 8px; margin-bottom: 12px;">
      {f'<div class="party-title" style="margin-bottom: 2px;">{party_line}</div>' if party_line else ''}
      {f'<div class="candidate-name">{candidate_disp}</div>' if candidate_disp else ''}
      <div class="ward-subtitle">{title}{page_suffix} · {place_hi}</div>
    </div>"""
    else:
        multi_header_html = f"""
    <div class="header-row" style="text-align: center; display: block; border-bottom: 2px solid #bfdbfe; padding-bottom: 8px; margin-bottom: 12px;">
      <div style="font-size: 11px; font-weight: 800; color: {theme_color}; text-transform: uppercase; letter-spacing: 0.8px;">
        डिजिटल मतदाता सेवा व चुनाव प्रबंधन प्रणाली
      </div>
      <div style="font-size: 22px; font-weight: 900; color: #1e3a8a; margin: 3px 0 2px; line-height: 1.2;">
        {title}{page_suffix}
      </div>
      <div class="ward-subtitle">
        {place_hi}
      </div>
    </div>"""

    is_ballot_election = bool(branding.get('is_ballot_election') or branding.get('voting_method') == 'BALLOT_PAPER')
    ballot_color = str(branding.get('ballot_color') or '').strip()
    ballot_color_badge = (
        f'<span class="ballot-color-pill" style="display:inline-block; margin-left:8px; padding:2px 8px; font-size:11px; font-weight:bold; border-radius:12px; background:#f1f5f9; color:#334155; border:1px solid #cbd5e1;">मतपत्र रंग: {ballot_color}</span>'
        if ballot_color else ''
    )

    if is_ballot_election:
        banner_title_html = f'<div class="ballot-banner-title">मतपत्र क्रम संख्या: {ballot_no}{ballot_color_badge}</div>'
        voter_guidance_text = "🙏 कृपया पहचान पत्र साथ लाएं · मतदान केंद्र पर दी गई मुहर का उपयोग करें · मतपत्र को पहले खड़े में मोड़ें"
    else:
        banner_title_html = f'<div class="ballot-banner-title">ईवीएम क्रम संख्या: {ballot_no}{ballot_color_badge}</div>'
        voter_guidance_text = "🙏 कृपया मतदान के दिन अपना पहचान पत्र (Voter ID / आधार कार्ड) साथ लाएं।"

    if has_ballot and has_symbol:
        multi_ballot_html = f"""
    <div class="ballot-banner">
      <div class="ballot-banner-left">
        <div class="ballot-big-num">{ballot_no}</div>
        <div class="ballot-banner-txt">
          {banner_title_html}
          <div class="ballot-banner-sub">{appeal_msg}</div>
        </div>
      </div>
      <div class="ballot-banner-symbol">
        <img src="{lotus_src}" alt="{symbol_name_hi}">
        <div class="ballot-banner-symbol-txt">{symbol_name_hi}</div>
      </div>
    </div>"""
    else:
        multi_ballot_html = ""

    if has_ballot and has_symbol:
        multi_footer_html = f"""
    <div class="footer-bar">
      <div class="slogan">🗳️ {slogan} 🗳️</div>
      <div class="footer-note">{voter_guidance_text}</div>
    </div>"""
    else:
        footer_guidance = voter_guidance_text if is_ballot_election else "🙏 कृपया मतदान दिवस पर अपना पहचान पत्र (Voter ID / आधार कार्ड) साथ लाएं।"
        multi_footer_html = f"""
    <div class="footer-bar" style="border-top: 1.5px solid #bfdbfe; padding-top: 8px;">
      <div class="slogan" style="color: {theme_color}; font-size: 13.5px; font-weight: 900; margin-bottom: 3px;">🗳️ {slogan} 🗳️</div>
      <div class="footer-note">{footer_guidance}</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{
      margin: 0;
      padding: 0;
      background: #ffffff;
      font-family: 'Segoe UI', Arial, sans-serif;
      overflow: hidden;
      width: 710px;
    }}
    ::-webkit-scrollbar {{ display: none !important; }}
    .card {{
      width: 710px;
      margin: 0;
      background: #ffffff;
      border: 3.5px solid {theme_color};
      border-radius: 14px;
      padding: 14px 16px;
      position: relative;
    }}
    .top-strip {{
      height: 6px;
      background: linear-gradient(90deg, #ff9933 0%, #ffffff 50%, #138808 100%);
      border-radius: 3px;
      margin-bottom: 12px;
    }}
    .header-row {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding-bottom: 12px;
      border-bottom: 2px solid #bfdbfe;
      margin-bottom: 12px;
    }}
    .symbol-badge {{
      width: 120px;
      height: 140px;
      border: 2.5px solid {theme_color};
      border-radius: 12px;
      background: #ffffff;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: space-between;
      box-shadow: 0 4px 10px rgba(37, 99, 235, 0.18);
      padding: 5px 6px;
      flex-shrink: 0;
      position: relative;
      box-sizing: border-box;
    }}
    .ballot-tag {{
      width: 100%;
      background: {theme_color};
      color: #ffffff;
      text-align: center;
      font-size: 13px;
      font-weight: 900;
      padding: 3px 0;
      border-radius: 6px;
      letter-spacing: 0.5px;
      line-height: 1.2;
    }}
    .ballot-tag.symbol-top-tag {{
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 0.6px;
      padding: 3px 0;
    }}
    .ballot-tag span {{
      background: #ffffff;
      color: {theme_color};
      padding: 1px 7px;
      border-radius: 4px;
      font-size: 15px;
      font-weight: 900;
      margin-left: 3px;
    }}
    .symbol-badge img {{
      width: 78px;
      height: 78px;
      max-width: 84px;
      max-height: 80px;
      object-fit: contain;
      margin: auto 0;
      display: block;
    }}
    .symbol-label {{
      font-size: 11.5px;
      font-weight: 800;
      color: {theme_color};
      background: #eff6ff;
      border: 1px solid #bfdbfe;
      border-radius: 5px;
      padding: 2px 6px;
      text-align: center;
      width: 100%;
      box-sizing: border-box;
      line-height: 1.2;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .candidate-badge {{
      width: 120px;
      height: 140px;
      border: 2.5px solid {theme_color};
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 10px rgba(0,0,0,0.15);
      position: relative;
      flex-shrink: 0;
      background: linear-gradient(180deg, #ffffff 0%, #eff6ff 100%);
      display: flex;
      align-items: flex-end;
      justify-content: center;
    }}
    .candidate-badge img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }}
    .header-text {{
      flex: 1;
      text-align: center;
    }}
    .party-title {{
      font-size: 13px;
      font-weight: 800;
      color: {theme_color};
      letter-spacing: 0.5px;
      text-transform: uppercase;
      margin: 0;
    }}
    .candidate-name {{
      font-size: 26px;
      font-weight: 900;
      color: #1e3a8a;
      margin: 4px 0 2px;
      line-height: 1.2;
    }}
    .ward-subtitle {{
      font-size: 12px;
      font-weight: 700;
      color: #475569;
      margin: 0;
    }}
    .ballot-banner {{
      background: linear-gradient(135deg, {theme_color} 0%, #1e40af 100%);
      border-radius: 10px;
      padding: 8px 14px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      color: #ffffff;
      margin-bottom: 12px;
      box-shadow: 0 4px 10px rgba(37, 99, 235, 0.25);
    }}
    .ballot-banner-left {{
      display: flex;
      align-items: center;
      gap: 10px;
    }}
    .ballot-big-num {{
      font-size: 32px;
      font-weight: 900;
      background: #ffffff;
      color: {theme_color};
      width: 44px;
      height: 44px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      line-height: 1;
      box-shadow: 0 2px 5px rgba(0,0,0,0.2);
    }}
    .ballot-banner-txt {{
      display: flex;
      flex-direction: column;
    }}
    .ballot-banner-title {{
      font-size: 16px;
      font-weight: 900;
      letter-spacing: 0.3px;
    }}
    .ballot-banner-sub {{
      font-size: 12px;
      font-weight: 700;
      opacity: 0.95;
    }}
    .ballot-banner-symbol {{
      display: flex;
      align-items: center;
      gap: 8px;
      background: #ffffff;
      border-radius: 8px;
      padding: 5px 12px;
      box-shadow: 0 2px 5px rgba(0,0,0,0.15);
    }}
    .ballot-banner-symbol img {{
      width: 32px;
      height: 32px;
      object-fit: contain;
    }}
    .ballot-banner-symbol-txt {{
      font-size: 14px;
      font-weight: 900;
      color: {theme_color};
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 10px;
    }}
    th {{
      background: #eff6ff;
      color: #1e3a8a;
      padding: {def_th_pad};
      border: 1.5px solid #bfdbfe;
      font-size: 13px;
      text-transform: uppercase;
      text-align: center;
      font-weight: 800;
    }}
    th.kramank-col {{
      background: #dbeafe;
      color: {theme_color};
      font-size: 14px;
    }}
    td {{
      padding: {def_td_pad};
      border: 1.5px solid #e2e8f0;
      vertical-align: middle;
    }}
    .kramank-cell {{
      text-align: center;
      font-weight: 900;
      font-size: {def_kramank_font};
      color: {theme_color};
      background: #eff6ff;
    }}
    .booth-box {{
      background: #ffffff;
      border: 1.5px solid #bfdbfe;
      border-left: 5px solid {theme_color};
      border-radius: 8px;
      padding: 8px 12px;
      font-size: 12.5px;
      margin-bottom: 12px;
    }}
    .booth-label {{
      color: {theme_color};
      font-weight: 800;
      font-size: 12px;
    }}
    .booth-name {{
      font-weight: 800;
      font-size: 13px;
      color: #0f172a;
      margin-top: 3px;
      line-height: 1.35;
    }}
    .footer-bar {{
      text-align: center;
      border-top: 1.5px solid #bfdbfe;
      padding-top: 8px;
    }}
    .slogan {{
      font-size: 13.5px;
      font-weight: 900;
      color: {theme_color};
      margin-bottom: 3px;
    }}
    .footer-note {{
      font-size: 10.5px;
      color: #64748b;
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="top-strip"></div>
    {multi_header_html}
    {multi_ballot_html}

    <table>
      <thead>
        <tr>
          <th style="width:72px;">{multi_part_col_title}</th>
          <th class="kramank-col" style="width:80px;">क्रम संख्या</th>
          <th>मतदाता का नाम एवं संबंध</th>
          <th style="width:60px;">मकान</th>
          <th style="width:75px;">आयु / लिंग</th>
          <th style="width:105px;">EPIC नं.</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>

    {booth_html}

    {multi_footer_html}
  </div>
</body>
</html>"""
