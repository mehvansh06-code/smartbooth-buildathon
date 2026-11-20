"""
electionapp/services/photoparchi_excel.py
High-Performance Bilingual Voter Excel (.xlsx) Generator for PhotoParchi.
Generates styled, auto-filtered spreadsheets for candidate canvassing.

Supports:
1. Single Gram Panchayat / Ward Panch (13-column single sheet).
2. Multi-Level Hierarchical Campaigns (Zila Parishad / Panchayat Samiti):
   - Tab 1: 📊 Executive Summary Dashboard (PS, GP, Wards, Counts, Hyperlinks).
   - Tab 2: 👥 Master Consolidated Roll (15-column layout including Block & GP).
   - Tabs 3..N: Dedicated Gram Panchayat Sub-Sheets (13-column layout for field workers).

CRITICAL INVARIANT:
Voter mobile numbers are STRICTLY EXCLUDED to protect SmartBooth proprietary database assets.
Only public electoral roll attributes are exported.
"""

import io
import os
import re
import logging
from typing import List, Dict, Any, Optional
from collections import defaultdict
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger("smartbooth.photoparchi_excel")

try:
    from electionapp.translit.rules import transliterate_name
except Exception:
    try:
        from ..translit.rules import transliterate_name
    except Exception:
        transliterate_name = lambda s: ""


def _resolve_elector_names(elector: Dict[str, Any]):
    """Ensures both Hindi and English names/relatives are populated via transliteration if English is missing in SEC rolls."""
    name_hi = str(elector.get("name_hi") or "").strip()
    name_en = str(elector.get("name_en") or "").strip()
    rel_hi = str(elector.get("rel_name_hi") or "").strip()
    rel_en = str(elector.get("rel_name_en") or "").strip()

    if not name_en and name_hi:
        name_en = transliterate_name(name_hi).strip()
    if not rel_en and rel_hi:
        rel_en = transliterate_name(rel_hi).strip()

    return name_hi, name_en, rel_hi, rel_en

# Single GP 13-column header specification (strictly no phone/mobile)
SINGLE_GP_HEADERS = [
    "वार्ड क्र.",
    "क्रम सं.",
    "पहचान पत्र (EPIC)",
    "मतदाता का नाम (Hindi)",
    "Voter Name (English)",
    "पिता / पति का नाम (Hindi)",
    "Relative Name (English)",
    "संबंध",
    "मकान नं.",
    "आयु",
    "लिंग",
    "मतदान केंद्र",
    "स्थिति (Status)"
]

# Hierarchical Multi-GP 15-column header specification (for Master Consolidated sheet)
CONSOLIDATED_HEADERS = [
    "पंचायत समिति",
    "ग्राम पंचायत",
    "वार्ड क्र.",
    "क्रम सं.",
    "पहचान पत्र (EPIC)",
    "मतदाता का नाम (Hindi)",
    "Voter Name (English)",
    "पिता / पति का नाम (Hindi)",
    "Relative Name (English)",
    "संबंध",
    "मकान नं.",
    "आयु",
    "लिंग",
    "मतदान केंद्र",
    "स्थिति (Status)"
]

# Shared Styling Definitions
FONT_TITLE = Font(name="Segoe UI", size=13, bold=True, color="FFFFFF")
FONT_HEADER = Font(name="Segoe UI", size=10, bold=True, color="FFFFFF")
FONT_REGULAR = Font(name="Segoe UI", size=9.5)
FONT_BOLD = Font(name="Segoe UI", size=9.5, bold=True)
FONT_LINK = Font(name="Segoe UI", size=9.5, color="1D4ED8", underline="single")
FONT_DELETED = Font(name="Segoe UI", size=9.5, color="991B1B", bold=True)
FONT_ACTIVE = Font(name="Segoe UI", size=9.5, color="065F46", bold=True)

FILL_NAVY = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
FILL_BLUE = PatternFill(start_color="2563EB", end_color="2563EB", fill_type="solid")
FILL_TEAL = PatternFill(start_color="0D9488", end_color="0D9488", fill_type="solid")
FILL_ZEBRA = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
FILL_TOTAL = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
FILL_DELETED = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")

BORDER_THIN = Border(
    left=Side(style="thin", color="CBD5E1"),
    right=Side(style="thin", color="CBD5E1"),
    top=Side(style="thin", color="CBD5E1"),
    bottom=Side(style="thin", color="CBD5E1")
)
BORDER_TOTAL = Border(
    left=Side(style="thin", color="94A3B8"),
    right=Side(style="thin", color="94A3B8"),
    top=Side(style="thin", color="94A3B8"),
    bottom=Side(style="double", color="1E293B")
)

# Precomputed column widths for instantaneous formatting (avoids scanning millions of cells)
SINGLE_GP_COL_WIDTHS = {
    "A": 10,  # वार्ड क्र.
    "B": 10,  # क्रम सं.
    "C": 18,  # पहचान पत्र (EPIC)
    "D": 22,  # मतदाता का नाम (Hindi)
    "E": 22,  # Voter Name (English)
    "F": 22,  # पिता / पति का नाम (Hindi)
    "G": 22,  # Relative Name (English)
    "H": 10,  # संबंध
    "I": 12,  # मकान नं.
    "J": 8,   # आयु
    "K": 8,   # लिंग
    "L": 28,  # मतदान केंद्र
    "M": 16   # स्थिति (Status)
}

CONSOLIDATED_COL_WIDTHS = {
    "A": 18,  # पंचायत समिति
    "B": 20,  # ग्राम पंचायत
    "C": 10,  # वार्ड क्र.
    "D": 10,  # क्रम सं.
    "E": 18,  # पहचान पत्र (EPIC)
    "F": 22,  # मतदाता का नाम (Hindi)
    "G": 22,  # Voter Name (English)
    "H": 22,  # पिता / पति का नाम (Hindi)
    "I": 22,  # Relative Name (English)
    "J": 10,  # संबंध
    "K": 12,  # मकान नं.
    "L": 8,   # आयु
    "M": 8,   # लिंग
    "N": 28,  # मतदान केंद्र
    "O": 16   # स्थिति (Status)
}

GP_SUMMARY_COL_WIDTHS = {
    "A": 12,  # वार्ड क्र.
    "B": 18,  # वार्ड विवरण
    "C": 14,  # कुल मतदाता
    "D": 12,  # पुरुष मतदाता
    "E": 14,  # महिला मतदाता
    "F": 14,  # विलोपित मतदाता
    "G": 16   # वार्ड शीट लिंक
}

DASHBOARD_COL_WIDTHS = {
    "A": 18,  # पंचायत समिति
    "B": 20,  # ग्राम पंचायत
    "C": 12,  # कुल वार्ड
    "D": 14,  # कुल मतदाता
    "E": 14,  # सक्रिय मतदाता
    "F": 12,  # पुरुष मतदाता
    "G": 14,  # महिला मतदाता
    "H": 14,  # विलोपित मतदाता
    "I": 16   # जी.पी. शीट लिंक
}


def _sanitize_sheet_title(name: str) -> str:
    """Sanitizes a name for an Excel worksheet title (max 31 chars, no special chars)."""
    clean = re.sub(r'[\:\\/\?\*\[\]]', '_', str(name or "")).strip()
    return clean[:30] if clean else "Sheet"


def _render_single_gp_sheet(
    ws,
    raw_electors: List[Dict[str, Any]],
    candidate_name: str,
    area_name: str,
    title: str = "👥 सम्पूर्ण सूची"
):
    """Renders a consolidated single-GP 13-column voter roll."""
    ws.title = title
    clean_cand = (candidate_name or "प्रत्याशी").strip()
    clean_area = (area_name or "निर्वाचन क्षेत्र").strip()

    # Title Block
    ws.merge_cells("A1:M1")
    t_cell = ws["A1"]
    active_count = sum(1 for e in raw_electors if str(e.get("status") or "Active").strip().lower() != "deleted")
    t_cell.value = f"🏛️ स्मार्टबूथ सम्पूर्ण मतदाता सूची — {clean_cand} • {clean_area} ({active_count:,} सक्रिय मतदाता)"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # Column Headers
    ws.append(SINGLE_GP_HEADERS)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_BLUE
        c.alignment = Alignment(horizontal="center", vertical="center")

    center_cols = {1, 2, 8, 9, 10, 11, 13}
    left_cols = {4, 5, 6, 7, 12}

    # Data Rows
    for idx, elector in enumerate(raw_electors, start=3):
        w_no = elector.get("ward_no") or ""
        sl_no = elector.get("sl_no") or ""
        epic = str(elector.get("epic_no") or "").strip().upper()
        name_hi, name_en, rel_hi, rel_en = _resolve_elector_names(elector)
        rel_type = str(elector.get("rel_type") or "पिता").strip()
        house = str(elector.get("house_no") or "").strip()
        age = elector.get("age") or ""
        gender = str(elector.get("gender") or "").strip()
        booth = str(elector.get("booth_name") or elector.get("booth_area") or "").strip()

        raw_status = str(elector.get("status") or "Active").strip()
        is_deleted = raw_status.lower() == "deleted"
        status_display = "विलोपित (Deleted)" if is_deleted else "सक्रिय (Active)"

        row = [
            w_no, sl_no, epic, name_hi, name_en,
            rel_hi, rel_en, rel_type, house, age, gender, booth, status_display
        ]
        ws.append(row)
        ws.row_dimensions[idx].height = 20

        is_even = (idx % 2 == 0)
        for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
            cell = ws.cell(row=idx, column=col_idx)
            cell.border = BORDER_THIN
            cell.font = FONT_REGULAR
            if is_deleted:
                cell.fill = FILL_DELETED
            elif is_even:
                cell.fill = FILL_ZEBRA
            if col_idx in center_cols:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx in left_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            if col_idx == 13:
                cell.font = FONT_DELETED if is_deleted else FONT_ACTIVE

    # Column Auto-Widths & Auto-Filter
    for col_letter, width in SINGLE_GP_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width

    total_rows = len(raw_electors) + 2
    ws.auto_filter.ref = f"A2:M{total_rows}"


def _render_ward_sheet(
    ws,
    electors: List[Dict[str, Any]],
    ward_no: int,
    gp_name: str,
    candidate_name: str,
    sheet_title: Optional[str] = None
):
    """
    Renders a dedicated 13-column voter sheet for a specific ward.
    - Sets sheet title to 'वार्ड {ward_no:02d}' (or sheet_title if provided).
    - Title banner: '🏛️ ग्राम पंचायत {gp_name} — वार्ड {ward_no} • {candidate_name} ({count} मतदाता)'
    - Populates 13-column single-GP headers (SINGLE_GP_HEADERS), auto-filter, zebra striping, status formatting.
    """
    w_int = int(ward_no) if str(ward_no).isdigit() else 1
    ws.title = sheet_title or _sanitize_sheet_title(f"वार्ड {w_int:02d}")
    clean_cand = (candidate_name or "प्रत्याशी").strip()
    clean_gp = (gp_name or "ग्राम पंचायत").strip()

    # Title Block
    ws.merge_cells("A1:M1")
    t_cell = ws["A1"]
    tot_active = sum(1 for e in electors if str(e.get("status") or "Active").strip().lower() != "deleted")
    t_cell.value = f"🏛️ ग्राम पंचायत {clean_gp} — वार्ड {w_int:02d} • {clean_cand} ({tot_active:,} सक्रिय मतदाता)"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # Column Headers
    ws.append(SINGLE_GP_HEADERS)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_BLUE
        c.alignment = Alignment(horizontal="center", vertical="center")

    center_cols = {1, 2, 8, 9, 10, 11, 13}
    left_cols = {4, 5, 6, 7, 12}

    # Data Rows
    for idx, elector in enumerate(electors, start=3):
        w_val = elector.get("ward_no") or w_int
        sl_no = elector.get("sl_no") or ""
        epic = str(elector.get("epic_no") or "").strip().upper()
        name_hi, name_en, rel_hi, rel_en = _resolve_elector_names(elector)
        rel_type = str(elector.get("rel_type") or "पिता").strip()
        house = str(elector.get("house_no") or "").strip()
        age = elector.get("age") or ""
        gender = str(elector.get("gender") or "").strip()
        booth = str(elector.get("booth_name") or elector.get("booth_area") or "").strip()

        raw_status = str(elector.get("status") or "Active").strip()
        is_deleted = raw_status.lower() == "deleted"
        status_display = "विलोपित (Deleted)" if is_deleted else "सक्रिय (Active)"

        row = [
            w_val, sl_no, epic, name_hi, name_en,
            rel_hi, rel_en, rel_type, house, age, gender, booth, status_display
        ]
        ws.append(row)
        ws.row_dimensions[idx].height = 20

        is_even = (idx % 2 == 0)
        for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
            cell = ws.cell(row=idx, column=col_idx)
            cell.border = BORDER_THIN
            cell.font = FONT_REGULAR
            if is_deleted:
                cell.fill = FILL_DELETED
            elif is_even:
                cell.fill = FILL_ZEBRA
            if col_idx in center_cols:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx in left_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            if col_idx == 13:
                cell.font = FONT_DELETED if is_deleted else FONT_ACTIVE

    # Column Auto-Widths & Auto-Filter
    for col_letter, width in SINGLE_GP_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width

    total_rows = len(electors) + 2
    ws.auto_filter.ref = f"A2:M{total_rows}"


def _render_single_gp_summary(
    ws,
    ward_groups: Dict[int, List[Dict[str, Any]]],
    gp_name: str,
    candidate_name: str,
    area_name: str,
    ward_sheet_map: Dict[int, str]
):
    """Renders Sheet 1: Executive Summary for Single GP listing each ward with hyperlinks."""
    ws.title = "📊 सारांश"
    clean_cand = (candidate_name or "प्रत्याशी").strip()
    clean_gp = (gp_name or area_name or "ग्राम पंचायत").strip()

    # Title Block
    ws.merge_cells("A1:G1")
    t_cell = ws["A1"]
    t_cell.value = f"🏛️ ग्राम पंचायत {clean_gp} — वार्ड-वार सारांश • {clean_cand}"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    headers = [
        "क्र.सं.",
        "वार्ड क्र.",
        "सक्रिय मतदाता",
        "पुरुष",
        "महिला",
        "विलोपित",
        "वार्ड सूची लिंक"
    ]
    ws.append(headers)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_TEAL
        c.alignment = Alignment(horizontal="center", vertical="center")

    total_active_all = 0
    total_deleted_all = 0
    total_male_all = 0
    total_female_all = 0

    sorted_wards = sorted(ward_groups.keys())
    for idx, w_no in enumerate(sorted_wards, start=3):
        electors = ward_groups[w_no]
        tot_active = sum(1 for e in electors if str(e.get("status") or "Active").strip().lower() != "deleted")
        tot_deleted = sum(1 for e in electors if str(e.get("status") or "").strip().lower() == "deleted")
        m_count = sum(1 for e in electors if str(e.get("gender") or "").strip() in ("पुरुष", "M", "Male") and str(e.get("status") or "Active").strip().lower() != "deleted")
        f_count = sum(1 for e in electors if str(e.get("gender") or "").strip() in ("महिला", "स्त्री", "F", "Female") and str(e.get("status") or "Active").strip().lower() != "deleted")

        total_active_all += tot_active
        total_deleted_all += tot_deleted
        total_male_all += m_count
        total_female_all += f_count

        sheet_title = ward_sheet_map.get(w_no, f"वार्ड {w_no:02d}")
        row_num = idx - 2
        row_data = [
            row_num,
            f"वार्ड {w_no:02d}",
            tot_active,
            m_count,
            f_count,
            tot_deleted,
            "वार्ड देखें ➔"
        ]
        ws.append(row_data)
        ws.row_dimensions[idx].height = 22

        is_even = (idx % 2 == 0)
        for col_idx in range(1, 8):
            cell = ws.cell(row=idx, column=col_idx)
            cell.border = BORDER_THIN
            cell.font = FONT_REGULAR
            if is_even:
                cell.fill = FILL_ZEBRA
            if col_idx in (1, 2, 3, 4, 5, 6):
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx == 7:
                cell.alignment = Alignment(horizontal="center", vertical="center")
                cell.font = FONT_LINK
                cell.hyperlink = f"#'{sheet_title}'!A1"
            if col_idx == 6 and tot_deleted > 0:
                cell.font = FONT_DELETED

    # Grand Total Row
    tot_row_idx = len(sorted_wards) + 3
    tot_row = [
        "कुल",
        f"{len(sorted_wards)} वार्ड",
        total_active_all,
        total_male_all,
        total_female_all,
        total_deleted_all,
        "—"
    ]
    ws.append(tot_row)
    ws.row_dimensions[tot_row_idx].height = 26
    for col_idx in range(1, 8):
        cell = ws.cell(row=tot_row_idx, column=col_idx)
        cell.font = FONT_BOLD
        cell.fill = FILL_TOTAL
        cell.border = BORDER_TOTAL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        if col_idx == 6 and total_deleted_all > 0:
            cell.font = FONT_DELETED

    for col_letter, width in GP_SUMMARY_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width


def _render_summary_dashboard(
    ws,
    gp_ward_groups: Any,
    candidate_name: str,
    area_name: str,
    ward_sheet_map: Dict[Any, str]
):
    """Renders Tab 1: Executive Summary Dashboard with hyperlinked navigation."""
    ws.title = "📊 सारांश"
    clean_cand = (candidate_name or "प्रत्याशी").strip()
    clean_area = (area_name or "जिला परिषद / पंचायत समिति").strip()

    # Title Block
    ws.merge_cells("A1:I1")
    t_cell = ws["A1"]
    t_cell.value = f"🏛️ स्मार्टबूथ निर्वाचन क्षेत्र सारांश — {clean_cand} • {clean_area}"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # Headers
    headers = [
        "क्र.सं.",
        "पंचायत समिति",
        "ग्राम पंचायत",
        "वार्ड क्र.",
        "सक्रिय मतदाता",
        "पुरुष",
        "महिला",
        "विलोपित",
        "मतदाता सूची लिंक"
    ]
    ws.append(headers)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(headers) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_TEAL
        c.alignment = Alignment(horizontal="center", vertical="center")

    total_active_all = 0
    total_deleted_all = 0
    total_male_all = 0
    total_female_all = 0
    total_wards_all = 0

    row_idx = 3

    # Normalization: ensure dict of dicts: gp_name -> ward_no -> electors
    normalized_groups: Dict[str, Dict[int, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    if gp_ward_groups:
        first_val = next(iter(gp_ward_groups.values()))
        if isinstance(first_val, list):
            # Dict[str, List[Dict[str, Any]]]
            for gp_n, e_list in gp_ward_groups.items():
                for e in e_list:
                    raw_w = str(e.get("ward_no") or "").strip()
                    w_no = int(raw_w) if raw_w.isdigit() else 1
                    normalized_groups[gp_n][w_no].append(e)
        else:
            normalized_groups = gp_ward_groups

    sorted_gps = sorted(normalized_groups.keys())
    for gp_name in sorted_gps:
        w_dict = normalized_groups[gp_name]
        sorted_wards = sorted(w_dict.keys())
        for w_no in sorted_wards:
            electors = w_dict[w_no]
            total_wards_all += 1
            ps_name = str(electors[0].get("panchayat_samiti") or electors[0].get("block") or area_name or "").strip()
            tot_active = sum(1 for e in electors if str(e.get("status") or "Active").strip().lower() != "deleted")
            tot_deleted = sum(1 for e in electors if str(e.get("status") or "").strip().lower() == "deleted")
            m_count = sum(1 for e in electors if str(e.get("gender") or "").strip() in ("पुरुष", "M", "Male") and str(e.get("status") or "Active").strip().lower() != "deleted")
            f_count = sum(1 for e in electors if str(e.get("gender") or "").strip() in ("महिला", "स्त्री", "F", "Female") and str(e.get("status") or "Active").strip().lower() != "deleted")

            total_active_all += tot_active
            total_deleted_all += tot_deleted
            total_male_all += m_count
            total_female_all += f_count

            sheet_title = ward_sheet_map.get((gp_name, w_no)) or ward_sheet_map.get(gp_name) or "Sheet"
            row_num = row_idx - 2
            row_data = [
                row_num,
                ps_name,
                gp_name,
                f"वार्ड {w_no:02d}",
                tot_active,
                m_count,
                f_count,
                tot_deleted,
                "शीट देखें ➔"
            ]
            ws.append(row_data)
            ws.row_dimensions[row_idx].height = 22

            is_even = (row_idx % 2 == 0)
            for col_idx in range(1, 10):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = BORDER_THIN
                cell.font = FONT_REGULAR
                if is_even:
                    cell.fill = FILL_ZEBRA
                if col_idx in (1, 4, 5, 6, 7, 8):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_idx in (2, 3):
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                elif col_idx == 9:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                    cell.font = FONT_LINK
                    cell.hyperlink = f"#'{sheet_title}'!A1"
                if col_idx == 8 and tot_deleted > 0:
                    cell.font = FONT_DELETED

            row_idx += 1

    # Grand Total Row
    tot_row = [
        "कुल",
        "—",
        f"{len(sorted_gps)} ग्राम पंचायतें",
        f"{total_wards_all} वार्ड",
        total_active_all,
        total_male_all,
        total_female_all,
        total_deleted_all,
        "—"
    ]
    ws.append(tot_row)
    ws.row_dimensions[row_idx].height = 26
    for col_idx in range(1, 10):
        cell = ws.cell(row=row_idx, column=col_idx)
        cell.font = FONT_BOLD
        cell.fill = FILL_TOTAL
        cell.border = BORDER_TOTAL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        if col_idx == 8 and total_deleted_all > 0:
            cell.font = FONT_DELETED

    for col_letter, width in DASHBOARD_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width


def _render_consolidated_master_sheet(
    ws,
    raw_electors: List[Dict[str, Any]],
    candidate_name: str,
    area_name: str
):
    """Renders Tab 2: Master Consolidated 15-Column Sheet (All electors with PS & GP)."""
    ws.title = "👥 सम्पूर्ण सूची"
    clean_cand = (candidate_name or "प्रत्याशी").strip()
    clean_area = (area_name or "निर्वाचन क्षेत्र").strip()

    # Title Block
    ws.merge_cells("A1:O1")
    t_cell = ws["A1"]
    t_cell.value = f"🏛️ स्मार्टबूथ सम्पूर्ण मतदाता सूची — {clean_cand} • {clean_area} ({len(raw_electors):,} मतदाता)"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # Column Headers
    ws.append(CONSOLIDATED_HEADERS)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(CONSOLIDATED_HEADERS) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_BLUE
        c.alignment = Alignment(horizontal="center", vertical="center")

    center_cols = {3, 4, 10, 11, 12, 13, 15}
    left_cols = {1, 2, 6, 7, 8, 9, 14}

    # Data Rows
    for idx, elector in enumerate(raw_electors, start=3):
        ps = str(elector.get("panchayat_samiti") or elector.get("block") or "").strip()
        gp = str(elector.get("gram_panchayat") or "").strip()
        w_no = elector.get("ward_no") or ""
        sl_no = elector.get("sl_no") or ""
        epic = str(elector.get("epic_no") or "").strip().upper()
        name_hi, name_en, rel_hi, rel_en = _resolve_elector_names(elector)
        rel_type = str(elector.get("rel_type") or "पिता").strip()
        house = str(elector.get("house_no") or "").strip()
        age = elector.get("age") or ""
        gender = str(elector.get("gender") or "").strip()
        booth = str(elector.get("booth_name") or elector.get("booth_area") or "").strip()

        raw_status = str(elector.get("status") or "Active").strip()
        is_deleted = raw_status.lower() == "deleted"
        status_display = "विलोपित (Deleted)" if is_deleted else "सक्रिय (Active)"

        row = [
            ps, gp, w_no, sl_no, epic, name_hi, name_en,
            rel_hi, rel_en, rel_type, house, age, gender, booth, status_display
        ]
        ws.append(row)
        ws.row_dimensions[idx].height = 20

        is_even = (idx % 2 == 0)
        for col_idx in range(1, len(CONSOLIDATED_HEADERS) + 1):
            cell = ws.cell(row=idx, column=col_idx)
            cell.border = BORDER_THIN
            cell.font = FONT_REGULAR
            if is_deleted:
                cell.fill = FILL_DELETED
            elif is_even:
                cell.fill = FILL_ZEBRA
            if col_idx in center_cols:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx in left_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            if col_idx == 15:
                cell.font = FONT_DELETED if is_deleted else FONT_ACTIVE

    # Column Auto-Widths & Auto-Filter
    for col_letter, width in CONSOLIDATED_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width

    total_rows = len(raw_electors) + 2
    ws.auto_filter.ref = f"A2:O{total_rows}"


def _render_gp_subsheet(
    ws,
    sheet_title: str,
    gp_name: str,
    electors: List[Dict[str, Any]],
    candidate_name: str
):
    """Renders a dedicated 13-column sub-sheet for a specific Gram Panchayat."""
    ws.title = sheet_title
    clean_cand = (candidate_name or "प्रत्याशी").strip()

    # Title Block
    ws.merge_cells("A1:M1")
    t_cell = ws["A1"]
    tot_active = sum(1 for e in electors if str(e.get("status") or "Active").strip().lower() != "deleted")
    t_cell.value = f"🏛️ ग्राम पंचायत {gp_name} — {clean_cand} ({tot_active:,} सक्रिय मतदाता)"
    t_cell.font = FONT_TITLE
    t_cell.fill = FILL_NAVY
    t_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 32

    # Column Headers
    ws.append(SINGLE_GP_HEADERS)
    ws.row_dimensions[2].height = 26
    for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
        c = ws.cell(row=2, column=col_idx)
        c.font = FONT_HEADER
        c.fill = FILL_BLUE
        c.alignment = Alignment(horizontal="center", vertical="center")

    center_cols = {1, 2, 8, 9, 10, 11, 13}
    left_cols = {4, 5, 6, 7, 12}

    for idx, elector in enumerate(electors, start=3):
        w_no = elector.get("ward_no") or ""
        sl_no = elector.get("sl_no") or ""
        epic = str(elector.get("epic_no") or "").strip().upper()
        name_hi, name_en, rel_hi, rel_en = _resolve_elector_names(elector)
        rel_type = str(elector.get("rel_type") or "पिता").strip()
        house = str(elector.get("house_no") or "").strip()
        age = elector.get("age") or ""
        gender = str(elector.get("gender") or "").strip()
        booth = str(elector.get("booth_name") or elector.get("booth_area") or "").strip()

        raw_status = str(elector.get("status") or "Active").strip()
        is_deleted = raw_status.lower() == "deleted"
        status_display = "विलोपित (Deleted)" if is_deleted else "सक्रिय (Active)"

        row = [
            w_no, sl_no, epic, name_hi, name_en,
            rel_hi, rel_en, rel_type, house, age, gender, booth, status_display
        ]
        ws.append(row)
        ws.row_dimensions[idx].height = 20

        is_even = (idx % 2 == 0)
        for col_idx in range(1, len(SINGLE_GP_HEADERS) + 1):
            cell = ws.cell(row=idx, column=col_idx)
            cell.border = BORDER_THIN
            cell.font = FONT_REGULAR
            if is_deleted:
                cell.fill = FILL_DELETED
            elif is_even:
                cell.fill = FILL_ZEBRA
            if col_idx in center_cols:
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif col_idx in left_cols:
                cell.alignment = Alignment(horizontal="left", vertical="center")
            if col_idx == 13:
                cell.font = FONT_DELETED if is_deleted else FONT_ACTIVE

    for col_letter, width in SINGLE_GP_COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width

    total_rows = len(electors) + 2
    ws.auto_filter.ref = f"A2:M{total_rows}"


def generate_single_ward_excel(
    electors: List[Dict[str, Any]],
    ward_no: int,
    candidate_name: str = "",
    area_name: str = "",
    gp_name: Optional[str] = None
) -> io.BytesIO:
    """
    Returns an io.BytesIO containing only that specific ward's Excel sheet.
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    target_gp = gp_name or area_name or "ग्राम पंचायत"
    w_int = int(ward_no) if str(ward_no).isdigit() else 1
    _render_ward_sheet(ws, electors, w_int, target_gp, candidate_name)
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def generate_voter_excel(
    raw_electors: List[Dict[str, Any]],
    candidate_name: str = "",
    area_name: str = "",
    campaign_type: str = "PANCHAYAT",
    output_path: Optional[str] = None
) -> io.BytesIO:
    """
    Generates a professionally styled bilingual voter Excel workbook (.xlsx).
    Automatically adapts structure based on campaign scope:
    - Single GP (Sarpanch / Ward Panch / single GP campaign):
      - Sheet 1: 📊 सारांश (Executive Summary with table listing each ward: Ward No, active voters, male, female, deleted voters, with clickable Excel hyperlinks #'वार्ड 01'!A1 to jump to that ward sheet).
      - Sheet 2: 👥 सम्पूर्ण सूची (Consolidated master roll of all electors in the GP).
      - Sheets 3..N: Dedicated Tab for each Ward (वार्ड 01, वार्ड 02, ...).
    - Multi-GP (Zila Parishad / Panchayat Samiti):
      - Sheet 1: 📊 सारांश (Dashboard summarizing each GP and its wards with counts & hyperlinks).
      - Sheet 2: 👥 सम्पूर्ण सूची (Master Consolidated roll across all GPs).
      - Subsequent sheets: Dedicated Tab for each constituent GP and Ward (e.g. GP_{gp_name}_W{ward_no:02d}).

    Guarantees:
    - 100% Devanagari Hindi font integrity via OpenXML.
    - Zero mobile numbers / zero contact phone fields written (Proprietary Data Asset Protection).
    - Auto-filters enabled across all columns.
    - Zebra striping and auto-fitted column widths.
    """
    wb = openpyxl.Workbook()
    ws_default = wb.active

    # Detect if multiple Gram Panchayats are present
    gp_groups = defaultdict(list)
    for e in raw_electors:
        gp_name = str(e.get("gram_panchayat") or area_name or "ग्राम_पंचायत").strip()
        gp_groups[gp_name].append(e)

    is_multi_gp = (
        len(gp_groups) > 1 or
        campaign_type in ("ZILA_PARISHAD", "PANCHAYAT_SAMITI") and len(gp_groups) >= 1
    )

    if not is_multi_gp:
        # Standard Single-GP Multi-Tab Flow
        ward_groups = defaultdict(list)
        for e in raw_electors:
            raw_w = str(e.get("ward_no") or "").strip()
            w_no = int(raw_w) if raw_w.isdigit() else 1
            ward_groups[w_no].append(e)

        gp_name = list(gp_groups.keys())[0] if gp_groups else (area_name or "ग्राम पंचायत")

        ward_sheet_map = {}
        for w_no in sorted(ward_groups.keys()):
            ward_sheet_map[w_no] = _sanitize_sheet_title(f"वार्ड {w_no:02d}")

        # Sheet 1: 📊 सारांश (Executive Summary)
        _render_single_gp_summary(ws_default, ward_groups, gp_name, candidate_name, area_name, ward_sheet_map)

        # Sheet 2: 👥 सम्पूर्ण सूची (Consolidated master roll of all electors in the GP)
        ws_cons = wb.create_sheet(title="👥 सम्पूर्ण सूची")
        _render_single_gp_sheet(ws_cons, raw_electors, candidate_name, gp_name, title="👥 सम्पूर्ण सूची")

        # Sheets 3..N: Dedicated Tab for each Ward
        for w_no in sorted(ward_groups.keys()):
            s_title = ward_sheet_map[w_no]
            ws_ward = wb.create_sheet(title=s_title)
            _render_ward_sheet(ws_ward, ward_groups[w_no], w_no, gp_name, candidate_name, sheet_title=s_title)

    else:
        # Multi-GP Flow (Panchayat Samiti / Zila Parishad)
        gp_ward_groups = defaultdict(lambda: defaultdict(list))
        for e in raw_electors:
            gp_name = str(e.get("gram_panchayat") or area_name or "ग्राम_पंचायत").strip()
            raw_w = str(e.get("ward_no") or "").strip()
            w_no = int(raw_w) if raw_w.isdigit() else 1
            gp_ward_groups[gp_name][w_no].append(e)

        # Build clean unique sheet titles for each (gp_name, ward_no)
        ward_sheet_map = {}
        used_titles = set(["📊 सारांश", "👥 सम्पूर्ण सूची"])
        for gp_name in sorted(gp_ward_groups.keys()):
            for w_no in sorted(gp_ward_groups[gp_name].keys()):
                base_title = _sanitize_sheet_title(f"GP_{gp_name[:20]}_W{w_no:02d}")
                title = base_title
                counter = 1
                while title in used_titles:
                    title = _sanitize_sheet_title(f"GP_{gp_name[:16]}_W{w_no:02d}_{counter}")
                    counter += 1
                used_titles.add(title)
                ward_sheet_map[(gp_name, w_no)] = title

        # Sheet 1: 📊 सारांश Dashboard
        _render_summary_dashboard(ws_default, gp_ward_groups, candidate_name, area_name, ward_sheet_map)

        # Sheet 2: 👥 सम्पूर्ण सूची (Master Consolidated roll across all GPs)
        ws_cons = wb.create_sheet(title="👥 सम्पूर्ण सूची")
        _render_consolidated_master_sheet(ws_cons, raw_electors, candidate_name, area_name)

        # Subsequent sheets: Dedicated Tab for each constituent GP and Ward
        for gp_name in sorted(gp_ward_groups.keys()):
            for w_no in sorted(gp_ward_groups[gp_name].keys()):
                s_title = ward_sheet_map[(gp_name, w_no)]
                ws_ward = wb.create_sheet(title=s_title)
                _render_ward_sheet(ws_ward, gp_ward_groups[gp_name][w_no], w_no, gp_name, candidate_name, sheet_title=s_title)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    if output_path:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(buf.getvalue())
            logger.info("Saved multi-level voter Excel to disk: %s", output_path)
        except Exception as e:
            logger.warning("Failed to save Excel to disk path %s: %s", output_path, e)

    return buf
