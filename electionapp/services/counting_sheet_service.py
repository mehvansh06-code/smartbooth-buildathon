"""
counting_sheet_service.py
Universal Vote Counting & Result Tally Sheet Service for SmartBooth.
Provides:
  - get_wards_counting_data()       - Aggregates live registered electors & turnout per ward/booth
  - render_counting_sheet_html()     - Generates interactive, formula-driven, print-ready A4 HTML
  - generate_counting_sheet_excel()  - Generates OpenPyXL workbook with formulas & bundle tally sheet
"""

import io
import json
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def get_wards_counting_data(conn, tid, cfg):
    """
    Retrieves ward-wise / booth-wise electors and turnout from database.
    Robust against both PostgreSQL (tenant partitions/voters) and SQLite (tests).
    """
    wards_rows = []
    
    # Try PostgreSQL parameterized query with tenant_id filter
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                COALESCE(ward_no, part_no) as ward_no, 
                part_no, 
                count(*) as total, 
                count(*) FILTER (WHERE LOWER(COALESCE(status,'')) <> 'deleted') as active, 
                count(*) FILTER (WHERE is_voted = TRUE) as voted 
            FROM voters 
            WHERE tenant_id = %s 
            GROUP BY COALESCE(ward_no, part_no), part_no 
            ORDER BY COALESCE(ward_no, part_no) ASC, part_no ASC
        """, (tid,))
        rows = cur.fetchall()
        for r in rows:
            if isinstance(r, dict):
                wards_rows.append(r)
            else:
                wards_rows.append({
                    "ward_no": r[0],
                    "part_no": r[1],
                    "total": r[2],
                    "active": r[3],
                    "voted": r[4]
                })
    except Exception:
        # Fallback for SQLite / test environments
        try:
            if hasattr(conn, 'rollback'):
                conn.rollback()
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    COALESCE(ward_no, part_no) as ward_no, 
                    part_no, 
                    count(*) as total
                FROM voters 
                GROUP BY COALESCE(ward_no, part_no), part_no 
                ORDER BY COALESCE(ward_no, part_no) ASC, part_no ASC
            """)
            rows = cur.fetchall()
            for r in rows:
                wno = r[0] if not isinstance(r, dict) else r.get('ward_no')
                pno = r[1] if not isinstance(r, dict) else r.get('part_no')
                tot = r[2] if not isinstance(r, dict) else r.get('total')
                wards_rows.append({
                    "ward_no": wno,
                    "part_no": pno,
                    "total": tot,
                    "active": tot,
                    "voted": 0
                })
        except Exception:
            # If voters table is empty or missing in test, return default 1..9
            wards_rows = []

    booth_cfg = cfg.get('booths', {}) or {}
    
    # If no rows found in DB, populate from booth_cfg or standard 9 wards
    if not wards_rows:
        if booth_cfg:
            for k, b_info in sorted(booth_cfg.items(), key=lambda x: int(x[0]) if x[0].isdigit() else str(x[0])):
                w_num = int(k) if k.isdigit() else 1
                wards_rows.append({
                    "ward_no": w_num,
                    "part_no": w_num,
                    "total": 0,
                    "active": 0,
                    "voted": 0
                })
        else:
            for i in range(1, 10):
                wards_rows.append({
                    "ward_no": i,
                    "part_no": i,
                    "total": 0,
                    "active": 0,
                    "voted": 0
                })

    structured_data = []
    for idx, w in enumerate(wards_rows, 1):
        wno = w.get('ward_no') or idx
        pno = w.get('part_no') or wno
        b_info = booth_cfg.get(str(pno)) or booth_cfg.get(str(wno)) or {}
        
        school = b_info.get('name') or f"मतदान केंद्र {pno}"
        area = b_info.get('area') or ""
        room = ""
        
        # Extract room if present in name
        if "कमरा" in school:
            parts = school.split("कमरा")
            school = parts[0].strip(" -,").strip()
            room = "कमरा " + parts[1].strip(" -,").strip()
        
        structured_data.append({
            "ward_no": wno,
            "round_no": idx,
            "booth_no": str(pno),
            "room_no": room or f"कक्ष {wno}",
            "area_name": area or "स्थानीय क्षेत्र",
            "school": school,
            "total_electors": w.get('active') or w.get('total') or 0,
            "voted_so_far": w.get('voted') or 0,
        })

    return structured_data


def render_counting_sheet_html(conn, tid, slug, cfg):
    """
    Renders standalone interactive + print-ready A4 HTML counting sheet.
    """
    wards_data = get_wards_counting_data(conn, tid, cfg)
    
    cand = cfg.get('candidate', {}) or {}
    party = cfg.get('party', {}) or {}
    elec = cfg.get('election', {}) or {}

    cand_name_hi = cand.get('name_hi') or cand.get('name_en') or 'प्रत्याशी'
    cand_name_en = cand.get('name_en') or ''
    symbol_name_hi = party.get('symbol_name_hi') or cand.get('symbol') or elec.get('symbol') or 'स्लेट'
    ballot_serial = party.get('ballot_serial') or '2'
    panchayat_name_hi = elec.get('ward_name_hi') or elec.get('ward_name_en') or slug.title()
    panchayat_samiti = cfg.get('panchayat_samiti') or ''
    district = elec.get('district') or ''
    total_active_electors = sum(w['total_electors'] for w in wards_data)
    total_wards = len(wards_data)
    
    wards_json = json.dumps(wards_data, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="hi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{panchayat_name_hi} — मतगणना सारणी एवं परिणाम पत्रक (Vote Counting Sheet)</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Hind:wght@400;500;600;700&family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --primary: #1e3a8a;
    --primary-dark: #0f172a;
    --orange: #ea580c;
    --emerald: #059669;
    --emerald-dark: #047857;
    --emerald-light: #ecfdf5;
    --rose: #e11d48;
    --rose-light: #fff1f2;
    --slate-bg: #f8fafc;
    --border: #cbd5e1;
  }}

  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
  }}

  body {{
    font-family: 'Hind', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background-color: var(--slate-bg);
    color: #0f172a;
    line-height: 1.4;
    padding: 16px;
  }}

  .container {{
    max-width: 1400px;
    margin: 0 auto;
    background: #ffffff;
    border-radius: 16px;
    box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.08), 0 8px 10px -6px rgba(0, 0, 0, 0.04);
    border: 1px solid var(--border);
    overflow: hidden;
  }}

  .header-banner {{
    background: linear-gradient(135deg, #0f172a 0%, #1e3a8a 100%);
    color: white;
    padding: 18px 24px;
    border-bottom: 3px solid var(--orange);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
  }}

  .header-title-block h1 {{
    font-size: 20px;
    font-weight: 800;
    letter-spacing: -0.02em;
    display: flex;
    align-items: center;
    gap: 10px;
  }}

  .header-subtitle {{
    font-size: 13px;
    color: #93c5fd;
    font-weight: 500;
    margin-top: 4px;
  }}

  .candidate-badge {{
    background: rgba(255, 255, 255, 0.12);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 12px;
    padding: 8px 16px;
    display: flex;
    align-items: center;
    gap: 14px;
  }}

  .symbol-tag {{
    background: var(--orange);
    color: white;
    font-size: 12px;
    font-weight: 800;
    padding: 4px 10px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 6px;
  }}

  .toolbar {{
    background: #f1f5f9;
    padding: 12px 24px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 12px;
  }}

  .btn-group {{
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }}

  .btn {{
    font-family: inherit;
    font-size: 12px;
    font-weight: 700;
    padding: 8px 16px;
    border-radius: 8px;
    border: none;
    cursor: pointer;
    transition: all 0.15s ease;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    text-decoration: none;
  }}

  .btn:active {{
    transform: scale(0.97);
  }}

  .btn-primary {{ background: var(--primary); color: white; }}
  .btn-primary:hover {{ background: #1d4ed8; }}

  .btn-success {{ background: var(--emerald); color: white; }}
  .btn-success:hover {{ background: var(--emerald-dark); }}

  .btn-outline {{
    background: white;
    color: #334155;
    border: 1px solid var(--border);
  }}
  .btn-outline:hover {{ background: #e2e8f0; }}

  .tab-pills {{
    display: flex;
    background: #e2e8f0;
    padding: 3px;
    border-radius: 10px;
    gap: 4px;
  }}

  .tab-btn {{
    padding: 6px 14px;
    border-radius: 7px;
    font-size: 12px;
    font-weight: 700;
    border: none;
    background: transparent;
    color: #475569;
    cursor: pointer;
  }}

  .tab-btn.active {{
    background: white;
    color: var(--primary-dark);
    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  }}

  .metrics-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    padding: 16px 24px;
    background: white;
    border-bottom: 1px solid var(--border);
  }}

  .metric-card {{
    background: var(--slate-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
  }}

  .metric-label {{
    font-size: 11px;
    font-weight: 700;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }}

  .metric-value {{
    font-size: 20px;
    font-weight: 900;
    color: #0f172a;
    margin-top: 2px;
    font-family: 'JetBrains Mono', monospace;
  }}

  .metric-card.lead-card {{
    background: var(--emerald-light);
    border-color: #a7f3d0;
  }}
  .metric-card.lead-card .metric-value {{
    color: var(--emerald-dark);
  }}

  .metric-card.deficit-card {{
    background: var(--rose-light);
    border-color: #fecdd3;
  }}
  .metric-card.deficit-card .metric-value {{
    color: var(--rose);
  }}

  .table-responsive {{
    overflow-x: auto;
    padding: 16px 24px;
  }}

  table.counting-table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    text-align: left;
  }}

  table.counting-table th {{
    background: #1e293b;
    color: #f8fafc;
    padding: 10px 8px;
    font-weight: 700;
    font-size: 11px;
    text-align: center;
    border: 1px solid #334155;
    vertical-align: middle;
  }}

  table.counting-table th.th-chetan {{
    background: var(--orange);
    color: white;
  }}

  table.counting-table th.th-lead {{
    background: var(--emerald-dark);
    color: white;
  }}

  table.counting-table td {{
    padding: 8px 6px;
    border: 1px solid var(--border);
    vertical-align: middle;
    text-align: center;
  }}

  table.counting-table tr:nth-child(even) {{
    background: #f8fafc;
  }}

  table.counting-table tr:hover {{
    background: #f1f5f9;
  }}

  .booth-cell {{
    text-align: left !important;
    font-size: 11px;
  }}

  .booth-school {{
    font-weight: 700;
    color: #0f172a;
  }}

  .booth-area {{
    color: #64748b;
    font-size: 10px;
  }}

  .vote-input {{
    width: 68px;
    padding: 6px 4px;
    border: 1.5px solid var(--border);
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    text-align: center;
    transition: all 0.15s ease;
  }}

  .vote-input:focus {{
    outline: none;
    border-color: var(--primary);
    box-shadow: 0 0 0 3px rgba(30, 58, 138, 0.15);
    background: #fff;
  }}

  .vote-input.chetan-input {{
    border-color: #fdba74;
    background: #fffaf0;
    color: #9a3412;
  }}
  .vote-input.chetan-input:focus {{
    border-color: var(--orange);
    box-shadow: 0 0 0 3px rgba(234, 88, 12, 0.2);
  }}

  .lead-badge {{
    display: inline-block;
    padding: 3px 8px;
    border-radius: 6px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    font-weight: 800;
  }}

  .lead-pos {{
    background: #dcfce7;
    color: #166534;
    border: 1px solid #86efac;
  }}

  .lead-neg {{
    background: #fee2e2;
    color: #991b1b;
    border: 1px solid #fca5a5;
  }}

  .lead-zero {{
    background: #f1f5f9;
    color: #475569;
  }}

  .total-row {{
    background: #0f172a !important;
    color: white;
    font-weight: 800;
    font-size: 13px;
  }}

  .total-row td {{
    border: 1px solid #334155;
    padding: 12px 6px;
  }}

  .total-row .highlight-green {{
    background: #064e3b;
    color: #6ee7b7;
    font-size: 15px;
  }}

  .sign-section {{
    padding: 24px;
    border-top: 1px solid var(--border);
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 24px;
    background: white;
  }}

  .sign-box {{
    border: 1px dashed var(--border);
    border-radius: 8px;
    padding: 16px;
    text-align: center;
  }}

  .sign-line {{
    margin-top: 40px;
    border-top: 1px solid #64748b;
    padding-top: 6px;
    font-weight: 700;
    font-size: 12px;
    color: #334155;
  }}

  @media print {{
    body {{
      background: #fff;
      padding: 0;
    }}
    .container {{
      border: none;
      box-shadow: none;
      max-width: 100%;
    }}
    .toolbar, .btn-group, .tab-pills, .no-print {{
      display: none !important;
    }}
    .header-banner {{
      padding: 10px 14px;
      border-bottom: 2px solid #000;
    }}
    .header-title-block h1 {{
      font-size: 15px;
      color: #000;
    }}
    .header-subtitle {{
      color: #333;
      font-size: 10px;
    }}
    .candidate-badge {{
      border: 1px solid #000;
      color: #000;
      padding: 4px 10px;
    }}
    .symbol-tag {{
      background: #000 !important;
      color: #fff !important;
    }}
    .metrics-grid {{
      padding: 8px 12px;
      gap: 6px;
    }}
    .metric-card {{
      padding: 4px 8px;
    }}
    .metric-value {{
      font-size: 14px;
    }}
    .table-responsive {{
      padding: 6px 0;
    }}
    table.counting-table {{
      font-size: 9.5px;
    }}
    table.counting-table th {{
      background: #1e3a8a !important;
      color: white !important;
      padding: 5px 3px;
      font-size: 8.5px;
    }}
    table.counting-table th.th-chetan {{
      background: #c2410c !important;
    }}
    table.counting-table td {{
      padding: 4px 3px;
    }}
    .vote-input {{
      border: none;
      background: transparent !important;
      font-size: 11px;
      width: 100%;
    }}
    .total-row {{
      background: #0f172a !important;
      color: #fff !important;
    }}
    .total-row td {{
      padding: 6px 3px;
    }}
    .sign-section {{
      padding: 12px 0;
      gap: 12px;
    }}
    .sign-line {{
      margin-top: 30px;
    }}
    @page {{
      size: A4 landscape;
      margin: 8mm 6mm;
    }}
  }}
</style>
</head>
<body>

<div class="container">

  <!-- HEADER BANNER -->
  <header class="header-banner">
    <div class="header-title-block">
      <h1>🗳️ {panchayat_name_hi} — मतगणना सारणी एवं परिणाम पत्रक</h1>
      <div class="header-subtitle">
        {panchayat_samiti}, जिला {district} · पंचायती राज चुनाव 2026
      </div>
    </div>
    
    <div class="candidate-badge">
      <div>
        <div style="font-size: 11px; opacity: 0.85;">सरपंच पद प्रत्याशी</div>
        <div style="font-size: 16px; font-weight: 900;">{cand_name_hi}</div>
      </div>
      <div class="symbol-tag">
        <span>चुनौ चिन्ह: {symbol_name_hi}</span>
        <span>(क्र. #{ballot_serial})</span>
      </div>
    </div>
  </header>

  <!-- ACTION TOOLBAR -->
  <div class="toolbar no-print">
    <div class="tab-pills">
      <button class="tab-btn active" onclick="switchView('roundTable')">📊 1. राउंड/वार्ड-वार सारणी</button>
      <button class="tab-btn" onclick="switchView('bundleTable')">📑 2. 50-मतपत्र बंडल टैली</button>
    </div>

    <div class="btn-group">
      <button class="btn btn-success" onclick="window.print()">
        <span>🖨️</span>
        <span>A4 प्रिंट करें (Print Sheet)</span>
      </button>

      <a class="btn btn-primary" href="/api/reports/export/excel?type=counting_sheet" download>
        <span>📊</span>
        <span>एक्सेल शीट (.xlsx) डाउनलोड</span>
      </a>

      <button class="btn btn-outline" onclick="loadSampleData()">
        <span>⚡</span>
        <span>डेमो टेस्ट भरें</span>
      </button>

      <button class="btn btn-outline" onclick="resetForm()" style="color: #e11d48;">
        <span>🔄</span>
        <span>रीसेट</span>
      </button>
    </div>
  </div>

  <!-- LIVE METRICS BAR -->
  <div class="metrics-grid">
    <div class="metric-card">
      <div class="metric-label">कुल पंजीकृत मतदाता</div>
      <div class="metric-value">{total_active_electors}</div>
    </div>

    <div class="metric-card">
      <div class="metric-label">कुल डाले गए मत</div>
      <div class="metric-value" id="statTotalPolled">0</div>
    </div>

    <div class="metric-card">
      <div class="metric-label">{cand_name_hi} को कुल मत</div>
      <div class="metric-value" id="statChetanVotes" style="color: var(--orange);">0</div>
    </div>

    <div class="metric-card">
      <div class="metric-label">निकटतम प्रतिद्वंद्वी मत</div>
      <div class="metric-value" id="statRunnerUpVotes">0</div>
    </div>

    <div class="metric-card lead-card" id="cardNetLead">
      <div class="metric-label" id="labelNetLead">अंतिम स्थिति (Lead/Deficit)</div>
      <div class="metric-value" id="statNetLead">0</div>
    </div>
  </div>

  <!-- VIEW 1: ROUND-WISE COUNTING TABLE -->
  <div id="viewRoundTable" class="table-responsive">
    <table class="counting-table" id="countingTable">
      <thead>
        <tr>
          <th style="width: 50px;">राउंड</th>
          <th style="width: 65px;">वार्ड</th>
          <th style="width: 250px;">मतदान केंद्र व कमरा (क्षेत्र)</th>
          <th style="width: 80px;">पंजीकृत मतदाता</th>
          <th style="width: 85px;">डाले गए मत</th>
          <th class="th-chetan" style="width: 95px;">{cand_name_hi}<br><small style="font-weight:500;">({symbol_name_hi} #{ballot_serial})</small></th>
          <th style="width: 95px;">प्रतिद्वंद्वी 1<br><input type="text" id="opp1_name" placeholder="नाम लिखें" style="font-size:9px; padding:1px; width:70px; text-align:center; border:none; border-radius:3px;"></th>
          <th style="width: 95px;">प्रतिद्वंद्वी 2<br><input type="text" id="opp2_name" placeholder="नाम लिखें" style="font-size:9px; padding:1px; width:70px; text-align:center; border:none; border-radius:3px;"></th>
          <th style="width: 75px;">अन्य / निर्दलीय</th>
          <th style="width: 70px;">खारिज मत</th>
          <th class="th-lead" style="width: 90px;">राउंड बढ़त (+)<br>घटत (-)</th>
          <th class="th-chetan" style="width: 95px;">संचयी कुल मत<br><small>({cand_name_hi})</small></th>
          <th class="th-lead" style="width: 100px;">कुल संचयी बढ़त (+)<br>घटत (-)</th>
          <th style="width: 80px;">एजेंट हस्ताक्षर</th>
        </tr>
      </thead>
      <tbody id="tableBody">
        <!-- Rendered dynamically -->
      </tbody>
      <tfoot>
        <tr class="total-row">
          <td colspan="3" style="text-align: center; font-size: 14px;">कुल महायोग (GRAND TOTAL)</td>
          <td id="totElectors">{total_active_electors}</td>
          <td id="totPolled">0</td>
          <td id="totChetan" style="background:#c2410c; font-size:15px;">0</td>
          <td id="totOpp1">0</td>
          <td id="totOpp2">0</td>
          <td id="totOthers">0</td>
          <td id="totInvalid">0</td>
          <td id="totRoundLead">--</td>
          <td id="totCumulativeChetan" class="highlight-green">0</td>
          <td id="totCumulativeMargin" class="highlight-green">--</td>
          <td style="font-size: 11px;">परिणाम घोषित</td>
        </tr>
      </tfoot>
    </table>
  </div>

  <!-- VIEW 2: 50-BALLOT BUNDLE TALLY TABLE -->
  <div id="viewBundleTable" class="table-responsive" style="display: none;">
    <div style="background: #eff6ff; padding: 12px; border: 1px solid #bfdbfe; border-radius: 8px; margin-bottom: 14px; font-size: 12px; color: #1e3a8a;">
      <strong>📌 50 मतपत्र बंडल गणना गाइड (Counting Table Protocol):</strong><br>
      राजस्थान पंचायती राज चुनाव में मतपेटी खोलने के पश्चात 50-50 मतपत्रों के बंडल बनाए जाते हैं। हमारे मतगणना अभिकर्ता (Counting Agents) प्रत्येक बंडल को सत्यापित करते समय नीचे चेकबॉक्स [✓] मार्क करें। किसी भी संदेहास्पद मत पर तत्काल आपत्ति (Objection) दर्ज कराएं।
    </div>

    <table class="counting-table">
      <thead>
        <tr>
          <th style="width: 60px;">वार्ड</th>
          <th style="width: 260px;">मतदान केंद्र व कमरा</th>
          <th style="width: 80px;">कुल मतदाता</th>
          <th>बंडल 1 (50)</th>
          <th>बंडल 2 (100)</th>
          <th>बंडल 3 (150)</th>
          <th>बंडल 4 (200)</th>
          <th>बंडल 5 (250)</th>
          <th>बंडल 6 (300)</th>
          <th>बंडल 7+ / खुले मत</th>
          <th style="width: 140px;">आपत्ति / संदेहास्पद मत</th>
        </tr>
      </thead>
      <tbody>
        {"".join([f'''
        <tr>
          <td style="font-weight:700;">वार्ड {w['ward_no']}</td>
          <td class="booth-cell">
            <div class="booth-school">{w['school']}, {w['room_no']}</div>
            <div class="booth-area">{w['area_name']}</div>
          </td>
          <td style="font-weight:700;">{w['total_electors']}</td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="checkbox" style="transform: scale(1.3);"></td>
          <td><input type="text" placeholder="खुले मत" style="width:55px; font-size:11px; text-align:center; padding:2px;"></td>
          <td><input type="text" placeholder="आपत्ति विवरण" style="width:110px; font-size:11px; padding:2px;"></td>
        </tr>
        ''' for w in wards_data])}
      </tbody>
    </table>
  </div>

  <!-- VERIFICATION & SIGNATURES -->
  <section class="sign-section">
    <div class="sign-box">
      <div style="font-size: 11px; color: #64748b;">अभिकर्ता द्वारा सत्यापन</div>
      <div class="sign-line">हस्ताक्षर मुख्य मतगणना अभिकर्ता (Chief Counting Agent)</div>
    </div>

    <div class="sign-box">
      <div style="font-size: 11px; color: #64748b;">प्रत्याशी प्रमाणीकरण</div>
      <div class="sign-line">{cand_name_hi} (सरपंच पद प्रत्याशी)</div>
    </div>

    <div class="sign-box">
      <div style="font-size: 11px; color: #64748b;">निर्वाचन अधिकारी अभिलेख</div>
      <div class="sign-line">हस्ताक्षर रिटर्निंग ऑफिसर (RO / ARO)</div>
    </div>
  </section>

</div>

<script>
const wardsData = {wards_json};
const STORAGE_KEY = "sb_counting_data_{slug}_v1";

function renderTable() {{
  const tbody = document.getElementById("tableBody");
  tbody.innerHTML = "";

  const saved = getSavedData();

  wardsData.forEach((w) => {{
    const r = w.round_no;
    const wSaved = saved[w.ward_no] || {{}};

    const votedVal = wSaved.voted !== undefined ? wSaved.voted : (w.voted_so_far || "");
    const chetanVal = wSaved.chetan !== undefined ? wSaved.chetan : "";
    const opp1Val = wSaved.opp1 !== undefined ? wSaved.opp1 : "";
    const opp2Val = wSaved.opp2 !== undefined ? wSaved.opp2 : "";
    const othersVal = wSaved.others !== undefined ? wSaved.others : "";
    const invalidVal = wSaved.invalid !== undefined ? wSaved.invalid : "";

    const tr = document.createElement("tr");
    tr.id = `row_${{w.ward_no}}`;
    tr.innerHTML = `
      <td style="font-weight: 800; font-family: 'JetBrains Mono', monospace;">R-${{r}}</td>
      <td style="font-weight: 800; color: #1e3a8a;">वार्ड ${{w.ward_no}}</td>
      <td class="booth-cell">
        <div class="booth-school">${{w.school}}, ${{w.room_no}}</div>
        <div class="booth-area">${{w.area_name}}</div>
      </td>
      <td style="font-weight: 700; font-family: 'JetBrains Mono', monospace;">${{w.total_electors}}</td>
      <td>
        <input type="number" class="vote-input" id="voted_${{w.ward_no}}" value="${{votedVal}}" placeholder="मत" oninput="calculate()">
      </td>
      <td>
        <input type="number" class="vote-input chetan-input" id="chetan_${{w.ward_no}}" value="${{chetanVal}}" placeholder="0" oninput="calculate()">
      </td>
      <td>
        <input type="number" class="vote-input" id="opp1_${{w.ward_no}}" value="${{opp1Val}}" placeholder="0" oninput="calculate()">
      </td>
      <td>
        <input type="number" class="vote-input" id="opp2_${{w.ward_no}}" value="${{opp2Val}}" placeholder="0" oninput="calculate()">
      </td>
      <td>
        <input type="number" class="vote-input" id="others_${{w.ward_no}}" value="${{othersVal}}" placeholder="0" oninput="calculate()">
      </td>
      <td>
        <input type="number" class="vote-input" id="invalid_${{w.ward_no}}" value="${{invalidVal}}" placeholder="0" oninput="calculate()">
      </td>
      <td id="round_lead_${{w.ward_no}}">
        <span class="lead-badge lead-zero">--</span>
      </td>
      <td id="cum_chetan_${{w.ward_no}}" style="font-weight: 800; font-family: 'JetBrains Mono', monospace; color: #c2410c;">
        --
      </td>
      <td id="cum_lead_${{w.ward_no}}">
        <span class="lead-badge lead-zero">--</span>
      </td>
      <td>
        <div style="font-size: 10px; color: #64748b; border-bottom: 1px dotted #94a3b8; height: 18px;"></div>
      </td>
    `;
    tbody.appendChild(tr);
  }});

  calculate();
}}

function getNum(id) {{
  const el = document.getElementById(id);
  if (!el || el.value === "") return null;
  const v = parseInt(el.value, 10);
  return isNaN(v) ? 0 : v;
}}

function calculate() {{
  let totPolled = 0;
  let totChetan = 0;
  let totOpp1 = 0;
  let totOpp2 = 0;
  let totOthers = 0;
  let totInvalid = 0;

  let cumChetan = 0;
  let cumOpp1 = 0;
  let cumOpp2 = 0;

  const toSave = {{}};

  wardsData.forEach((w) => {{
    const wid = w.ward_no;
    const voted = getNum(`voted_${{wid}}`);
    const chetan = getNum(`chetan_${{wid}}`);
    const opp1 = getNum(`opp1_${{wid}}`);
    const opp2 = getNum(`opp2_${{wid}}`);
    const others = getNum(`others_${{wid}}`);
    const invalid = getNum(`invalid_${{wid}}`);

    toSave[wid] = {{
      voted: voted !== null ? voted : "",
      chetan: chetan !== null ? chetan : "",
      opp1: opp1 !== null ? opp1 : "",
      opp2: opp2 !== null ? opp2 : "",
      others: others !== null ? others : "",
      invalid: invalid !== null ? invalid : "",
    }};

    if (voted !== null) totPolled += voted;
    if (chetan !== null) totChetan += chetan;
    if (opp1 !== null) totOpp1 += opp1;
    if (opp2 !== null) totOpp2 += opp2;
    if (others !== null) totOthers += others;
    if (invalid !== null) totInvalid += invalid;

    const roundLeadCell = document.getElementById(`round_lead_${{wid}}`);
    const cumChetanCell = document.getElementById(`cum_chetan_${{wid}}`);
    const cumLeadCell = document.getElementById(`cum_lead_${{wid}}`);

    if (chetan !== null) {{
      const maxOpp = Math.max(opp1 || 0, opp2 || 0, others || 0);
      const diff = chetan - maxOpp;

      let rBadge = "";
      if (diff > 0) {{
        rBadge = `<span class="lead-badge lead-pos">+${{diff}} बढ़त</span>`;
      }} else if (diff < 0) {{
        rBadge = `<span class="lead-badge lead-neg">${{diff}} घटत</span>`;
      }} else {{
        rBadge = `<span class="lead-badge lead-zero">बराबर (0)</span>`;
      }}
      roundLeadCell.innerHTML = rBadge;

      cumChetan += chetan;
      cumOpp1 += (opp1 || 0);
      cumOpp2 += (opp2 || 0);

      cumChetanCell.innerText = cumChetan;

      const maxCumOpp = Math.max(cumOpp1, cumOpp2);
      const cumDiff = cumChetan - maxCumOpp;

      let cBadge = "";
      if (cumDiff > 0) {{
        cBadge = `<span class="lead-badge lead-pos">+${{cumDiff}} बढ़त</span>`;
      }} else if (cumDiff < 0) {{
        cBadge = `<span class="lead-badge lead-neg">${{cumDiff}} घटत</span>`;
      }} else {{
        cBadge = `<span class="lead-badge lead-zero">0</span>`;
      }}
      cumLeadCell.innerHTML = cBadge;
    }} else {{
      roundLeadCell.innerHTML = `<span class="lead-badge lead-zero">--</span>`;
      cumChetanCell.innerText = cumChetan > 0 ? cumChetan : "--";
      cumLeadCell.innerHTML = `<span class="lead-badge lead-zero">--</span>`;
    }}
  }});

  // Grand totals
  document.getElementById("totPolled").innerText = totPolled;
  document.getElementById("totChetan").innerText = totChetan;
  document.getElementById("totOpp1").innerText = totOpp1;
  document.getElementById("totOpp2").innerText = totOpp2;
  document.getElementById("totOthers").innerText = totOthers;
  document.getElementById("totInvalid").innerText = totInvalid;

  const runnerUp = Math.max(totOpp1, totOpp2);
  const netMargin = totChetan - runnerUp;

  document.getElementById("statTotalPolled").innerText = totPolled;
  document.getElementById("statChetanVotes").innerText = totChetan;
  document.getElementById("statRunnerUpVotes").innerText = runnerUp;

  const cardLead = document.getElementById("cardNetLead");
  const statLead = document.getElementById("statNetLead");
  const labelLead = document.getElementById("labelNetLead");

  if (totChetan > 0 || runnerUp > 0) {{
    if (netMargin > 0) {{
      cardLead.className = "metric-card lead-card";
      labelLead.innerText = "🏆 वर्तमान कुल बढ़त (VICTORY LEAD)";
      statLead.innerText = `+${{netMargin}} मत`;
      document.getElementById("totCumulativeMargin").innerHTML = `<span class="lead-badge lead-pos" style="font-size:14px;">+${{netMargin}} बढ़त</span>`;
    }} else if (netMargin < 0) {{
      cardLead.className = "metric-card deficit-card";
      labelLead.innerText = "⚠️ वर्तमान अंतर (DEFICIT)";
      statLead.innerText = `${{netMargin}} मत`;
      document.getElementById("totCumulativeMargin").innerHTML = `<span class="lead-badge lead-neg" style="font-size:14px;">${{netMargin}} घटत</span>`;
    }} else {{
      cardLead.className = "metric-card";
      labelLead.innerText = "बराबर मुकाबला (TIE)";
      statLead.innerText = "0 मत";
      document.getElementById("totCumulativeMargin").innerText = "0";
    }}
  }} else {{
    cardLead.className = "metric-card";
    labelLead.innerText = "अंतिम स्थिति (Lead/Deficit)";
    statLead.innerText = "0";
    document.getElementById("totCumulativeMargin").innerText = "--";
  }}

  localStorage.setItem(STORAGE_KEY, JSON.stringify(toSave));
}}

function getSavedData() {{
  try {{
    const s = localStorage.getItem(STORAGE_KEY);
    return s ? JSON.parse(s) : {{}};
  }} catch(e) {{
    return {{}};
  }}
}}

function resetForm() {{
  if (confirm("क्या आप दर्ज किए गए सभी मत आंकड़े रीसेट करना चाहते हैं?")) {{
    localStorage.removeItem(STORAGE_KEY);
    renderTable();
  }}
}}

function loadSampleData() {{
  const sample = {{}};
  wardsData.forEach((w) => {{
    const tot = w.total_electors || 300;
    const voted = Math.round(tot * 0.82);
    const chetan = Math.round(voted * 0.58);
    const opp1 = Math.round(voted * 0.35);
    const opp2 = Math.round(voted * 0.04);
    const others = Math.round(voted * 0.02);
    const invalid = voted - (chetan + opp1 + opp2 + others);
    sample[w.ward_no] = {{
      voted: voted,
      chetan: chetan,
      opp1: opp1,
      opp2: opp2,
      others: others,
      invalid: invalid > 0 ? invalid : 1
    }};
  }});
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sample));
  renderTable();
}}

function switchView(view) {{
  document.getElementById("viewRoundTable").style.display = view === 'roundTable' ? 'block' : 'none';
  document.getElementById("viewBundleTable").style.display = view === 'bundleTable' ? 'block' : 'none';
  const tabs = document.querySelectorAll(".tab-btn");
  tabs[0].classList.toggle("active", view === 'roundTable');
  tabs[1].classList.toggle("active", view === 'bundleTable');
}}

document.addEventListener("DOMContentLoaded", renderTable);
</script>

</body>
</html>"""


def generate_counting_sheet_excel(conn, tid, slug, cfg):
    """
    Builds and returns an in-memory openpyxl workbook with auto formulas.
    """
    wards_data = get_wards_counting_data(conn, tid, cfg)
    
    cand = cfg.get('candidate', {}) or {}
    party = cfg.get('party', {}) or {}
    elec = cfg.get('election', {}) or {}

    cand_name_hi = cand.get('name_hi') or cand.get('name_en') or 'प्रत्याशी'
    cand_name_en = cand.get('name_en') or ''
    symbol_name_hi = party.get('symbol_name_hi') or cand.get('symbol') or elec.get('symbol') or 'स्लेट'
    ballot_serial = party.get('ballot_serial') or '2'
    panchayat_name_hi = elec.get('ward_name_hi') or elec.get('ward_name_en') or slug.title()
    panchayat_samiti = cfg.get('panchayat_samiti') or ''
    district = elec.get('district') or ''
    total_active_electors = sum(w['total_electors'] for w in wards_data)
    total_wards = len(wards_data)

    wb = openpyxl.Workbook()
    
    ws1 = wb.active
    ws1.title = "राउंड-वार मतगणना सारणी"
    ws1.views.sheetView[0].showGridLines = True
    
    NAVY = "1E3A8A"
    ORANGE = "EA580C"
    LIGHT_BLUE = "EFF6FF"
    BORDER_COLOR = "94A3B8"
    
    thin_border = Border(
        left=Side(style='thin', color=BORDER_COLOR),
        right=Side(style='thin', color=BORDER_COLOR),
        top=Side(style='thin', color=BORDER_COLOR),
        bottom=Side(style='thin', color=BORDER_COLOR)
    )
    double_bottom_border = Border(
        left=Side(style='thin', color=BORDER_COLOR),
        right=Side(style='thin', color=BORDER_COLOR),
        top=Side(style='thin', color=BORDER_COLOR),
        bottom=Side(style='double', color="0F172A")
    )
    
    ws1.merge_cells("A1:N1")
    ws1["A1"] = f"{panchayat_name_hi} ({panchayat_samiti}, जिला {district}) — मतगणना सारणी पत्रक"
    ws1["A1"].font = Font(name="Calibri", size=16, bold=True, color="FFFFFF")
    ws1["A1"].fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    ws1["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[1].height = 36
    
    ws1.merge_cells("A2:N2")
    ws1["A2"] = f"प्रत्याशी: {cand_name_hi} ({cand_name_en}) | चुनाव चिन्ह: {symbol_name_hi} | मतपत्र क्र.: #{ballot_serial} | कुल पंजीकृत मतदाता: {total_active_electors} | कुल वार्ड: {total_wards}"
    ws1["A2"].font = Font(name="Calibri", size=11, bold=True, color="1E3A8A")
    ws1["A2"].fill = PatternFill(start_color=LIGHT_BLUE, end_color=LIGHT_BLUE, fill_type="solid")
    ws1["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws1.row_dimensions[2].height = 24
    
    headers = [
        "राउंड क्र.", "वार्ड सं.", "मतदान केंद्र व कमरा", "कुल मतदाता", "कुल मत पड़े",
        f"{cand_name_hi}\n({symbol_name_hi} #{ballot_serial})", "प्रतिद्वंद्वी 1\n(नाम लिखें)",
        "प्रतिद्वंद्वी 2\n(नाम लिखें)", "अन्य / निर्दलीय", "खारिज/निरस्त मत",
        "राउंड बढ़त (+)\nघटत (-)", f"संचयी मत\n({cand_name_hi})", "कुल संचयी बढ़त (+)\nघटत (-)", "एजेंट हस्ताक्षर"
    ]
    
    ws1.row_dimensions[4].height = 32
    for col_idx, h in enumerate(headers, 1):
        cell = ws1.cell(row=4, column=col_idx)
        cell.value = h
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        if cand_name_hi in h:
            cell.fill = PatternFill(start_color=ORANGE, end_color=ORANGE, fill_type="solid")
        elif "संचयी" in h or "बढ़त" in h:
            cell.fill = PatternFill(start_color="047857", end_color="047857", fill_type="solid")
        else:
            cell.fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
            
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border
        
    start_row = 5
    for i, w in enumerate(wards_data):
        r = start_row + i
        ws1.row_dimensions[r].height = 24
        booth_loc = f"{w['school']}, {w['room_no']} ({w['area_name']})"
        
        ws1.cell(row=r, column=1, value=w['round_no']).alignment = Alignment(horizontal="center", vertical="center")
        ws1.cell(row=r, column=2, value=f"वार्ड {w['ward_no']}").alignment = Alignment(horizontal="center", vertical="center")
        ws1.cell(row=r, column=3, value=booth_loc).alignment = Alignment(horizontal="left", vertical="center")
        ws1.cell(row=r, column=4, value=w['total_electors']).alignment = Alignment(horizontal="center", vertical="center")
        
        v_cell = ws1.cell(row=r, column=5)
        if w['voted_so_far'] > 0:
            v_cell.value = w['voted_so_far']
        v_cell.alignment = Alignment(horizontal="center", vertical="center")
        
        for col_idx in [6, 7, 8, 9, 10]:
            c_cell = ws1.cell(row=r, column=col_idx)
            c_cell.alignment = Alignment(horizontal="center", vertical="center")
            
        ws1.cell(row=r, column=11, value=f"=IF(F{r}=\"\",\"\",F{r}-MAX(G{r},H{r},I{r}))")
        ws1.cell(row=r, column=11).alignment = Alignment(horizontal="center", vertical="center")
        ws1.cell(row=r, column=11).font = Font(bold=True)
        
        if i == 0:
            ws1.cell(row=r, column=12, value=f"=IF(F{r}=\"\",\"\",F{r})")
            ws1.cell(row=r, column=13, value=f"=IF(K{r}=\"\",\"\",K{r})")
        else:
            prev_r = r - 1
            ws1.cell(row=r, column=12, value=f"=IF(F{r}=\"\",L{prev_r},L{prev_r}+F{r})")
            ws1.cell(row=r, column=13, value=f"=IF(K{r}=\"\",M{prev_r},M{prev_r}+K{r})")
            
        ws1.cell(row=r, column=12).alignment = Alignment(horizontal="center", vertical="center")
        ws1.cell(row=r, column=12).font = Font(bold=True, color="047857")
        ws1.cell(row=r, column=13).alignment = Alignment(horizontal="center", vertical="center")
        ws1.cell(row=r, column=13).font = Font(bold=True)
        
        ws1.cell(row=r, column=14, value="").alignment = Alignment(horizontal="center", vertical="center")
        for col_idx in range(1, 15):
            ws1.cell(row=r, column=col_idx).border = thin_border
            
    tot_row = start_row + len(wards_data)
    ws1.row_dimensions[tot_row].height = 28
    ws1.merge_cells(f"A{tot_row}:C{tot_row}")
    ws1.cell(row=tot_row, column=1, value="कुल महायोग (GRAND TOTAL)")
    ws1.cell(row=tot_row, column=1).font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    ws1.cell(row=tot_row, column=1).fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    ws1.cell(row=tot_row, column=1).alignment = Alignment(horizontal="center", vertical="center")
    
    ws1.cell(row=tot_row, column=4, value=f"=SUM(D{start_row}:D{tot_row-1})")
    ws1.cell(row=tot_row, column=5, value=f"=SUM(E{start_row}:E{tot_row-1})")
    ws1.cell(row=tot_row, column=6, value=f"=SUM(F{start_row}:F{tot_row-1})")
    ws1.cell(row=tot_row, column=7, value=f"=SUM(G{start_row}:G{tot_row-1})")
    ws1.cell(row=tot_row, column=8, value=f"=SUM(H{start_row}:H{tot_row-1})")
    ws1.cell(row=tot_row, column=9, value=f"=SUM(I{start_row}:I{tot_row-1})")
    ws1.cell(row=tot_row, column=10, value=f"=SUM(J{start_row}:J{tot_row-1})")
    ws1.cell(row=tot_row, column=11, value=f"=SUM(K{start_row}:K{tot_row-1})")
    ws1.cell(row=tot_row, column=12, value=f"=F{tot_row}")
    ws1.cell(row=tot_row, column=13, value=f"=M{tot_row-1}")
    ws1.cell(row=tot_row, column=14, value="निर्वाचन परिणाम")
    
    for c in range(1, 15):
        cell = ws1.cell(row=tot_row, column=c)
        cell.font = Font(bold=True, size=11)
        cell.border = double_bottom_border
        if c in [6, 12, 13]:
            cell.fill = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
            cell.font = Font(bold=True, color="047857", size=12)
        elif c > 1:
            cell.fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    col_widths = {
        "A": 10, "B": 10, "C": 38, "D": 13, "E": 13, "F": 16,
        "G": 15, "H": 15, "I": 15, "J": 14, "K": 16, "L": 16, "M": 18, "N": 16
    }
    for col, width in col_widths.items():
        ws1.column_dimensions[col].width = width

    # Sheet 2: Bundle Tally
    ws2 = wb.create_sheet(title="50 मतपत्र बंडल टैली शीट")
    ws2.views.sheetView[0].showGridLines = True
    
    ws2.merge_cells("A1:K1")
    ws2["A1"] = f"{panchayat_name_hi} — मतगणना टेबल 50-मतपत्र बंडल सत्यापन पत्रक"
    ws2["A1"].font = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    ws2["A1"].fill = PatternFill(start_color=NAVY, end_color=NAVY, fill_type="solid")
    ws2["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[1].height = 32
    
    ws2.merge_cells("A2:K2")
    ws2["A2"] = f"प्रत्याशी: {cand_name_hi} [{symbol_name_hi} #{ballot_serial}] | मतगणना टेबल पर 50-50 मतपत्रों के बंडल बनाते समय टिक करें [✓]"
    ws2["A2"].font = Font(name="Calibri", size=10, bold=True, color="1E3A8A")
    ws2["A2"].fill = PatternFill(start_color=LIGHT_BLUE, end_color=LIGHT_BLUE, fill_type="solid")
    ws2["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws2.row_dimensions[2].height = 22
    
    b_headers = [
        "वार्ड", "मतदान केंद्र", "कुल मत", "बंडल 1 (50)", "बंडल 2 (100)", "बंडल 3 (150)", 
        "बंडल 4 (200)", "बंडल 5 (250)", "बंडल 6 (300)", "बंडल 7+ / खुले मत", "आपत्ति / संदेहास्पद मत"
    ]
    ws2.row_dimensions[4].height = 26
    for c_idx, bh in enumerate(b_headers, 1):
        cell = ws2.cell(row=4, column=c_idx, value=bh)
        cell.font = Font(bold=True, color="FFFFFF", size=10)
        cell.fill = PatternFill(start_color="334155", end_color="334155", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = thin_border
        
    for idx, w in enumerate(wards_data):
        rw = 5 + idx
        ws2.row_dimensions[rw].height = 24
        ws2.cell(row=rw, column=1, value=f"वार्ड {w['ward_no']}").alignment = Alignment(horizontal="center", vertical="center")
        ws2.cell(row=rw, column=2, value=f"{w['school']}, {w['room_no']}").alignment = Alignment(horizontal="left", vertical="center")
        ws2.cell(row=rw, column=3, value=w['total_electors']).alignment = Alignment(horizontal="center", vertical="center")
        for col_b in range(4, 12):
            c_b = ws2.cell(row=rw, column=col_b, value="[   ]")
            c_b.alignment = Alignment(horizontal="center", vertical="center")
            c_b.border = thin_border
        for c in range(1, 4):
            ws2.cell(row=rw, column=c).border = thin_border

    b_widths = {"A": 10, "B": 35, "C": 12, "D": 14, "E": 14, "F": 14, "G": 14, "H": 14, "I": 14, "J": 18, "K": 22}
    for col, width in b_widths.items():
        ws2.column_dimensions[col].width = width

    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream
