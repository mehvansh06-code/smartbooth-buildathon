"""
voter_ingest.py
High-Performance Multi-Tenant Voter Roll Ingestion Engine.
Supports CSV (with auto-detected encodings utf-8-sig, utf-8, latin1) and Excel (.xlsx, .xls).
Handles bilingual English & Hindi headers, normalizes attributes, and batch inserts/upserts
directly into PostgreSQL 17 partitions (voters_t{tenant_id}) or per-tenant SQLite fallback.
"""

import os
import io
import re
import csv
import json
from typing import Dict, Any, List, Optional, Tuple, Union

# Mapping from raw/bilingual header variations to canonical fields
HEADER_MAPPING = {
    # Part No / Booth No
    'part_no': 'part_no',
    'part': 'part_no',
    'partno': 'part_no',
    'part_number': 'part_no',
    'booth_no': 'part_no',
    'booth': 'part_no',
    'boothno': 'part_no',
    'भाग': 'part_no',
    'भाग_संख्या': 'part_no',
    'भागसंख्या': 'part_no',
    'भाग_नं': 'part_no',
    'भाग_क्र': 'part_no',
    'बूथ_संख्या': 'part_no',
    'बूथ': 'part_no',
    'बूथ_नं': 'part_no',

    # Serial No
    'sl_no': 'sl_no',
    'slno': 'sl_no',
    'serial_no': 'sl_no',
    'serialno': 'sl_no',
    'sr_no': 'sl_no',
    'srno': 'sl_no',
    'sno': 'sl_no',
    's_no': 'sl_no',
    'क्रम': 'sl_no',
    'क्रम_संख्या': 'sl_no',
    'क्रमसंख्या': 'sl_no',
    'क्रमांक': 'sl_no',
    'क्रम_सं': 'sl_no',
    'सरल_क्रमांक': 'sl_no',

    # EPIC / Voter Card
    'epic_no': 'epic_no',
    'epic': 'epic_no',
    'epicno': 'epic_no',
    'voter_id': 'epic_no',
    'voter_id_no': 'epic_no',
    'card_no': 'epic_no',
    'पहचान_पत्र': 'epic_no',
    'पहचान_पत्र_संख्या': 'epic_no',
    'पहचान_पत्र_क्रमांक': 'epic_no',
    'पहचानपत्र': 'epic_no',
    'पहचान_कार्ड': 'epic_no',

    # Name English
    'name_en': 'name_en',
    'name': 'name_en',
    'elector_name_en': 'name_en',
    'elector_name': 'name_en',
    'voter_name_en': 'name_en',
    'voter_name': 'name_en',
    'full_name_en': 'name_en',
    'full_name': 'name_en',
    'नाम_अंग्रेजी': 'name_en',
    'अंग्रेजी_नाम': 'name_en',

    # Name Hindi
    'name_hi': 'name_hi',
    'elector_name_hi': 'name_hi',
    'voter_name_hi': 'name_hi',
    'full_name_hi': 'name_hi',
    'नाम': 'name_hi',
    'मतदाता_का_नाम': 'name_hi',
    'नाम_हिंदी': 'name_hi',
    'नाम_हिन्दी': 'name_hi',

    # Relative Name English
    'relative_name_en': 'relative_name_en',
    'rel_name_en': 'relative_name_en',
    'father_name_en': 'relative_name_en',
    'husband_name_en': 'relative_name_en',
    'guardian_name_en': 'relative_name_en',
    'संबंधी_का_नाम_अंग्रेजी': 'relative_name_en',

    # Relative Name Hindi
    'relative_name_hi': 'relative_name_hi',
    'rel_name_hi': 'relative_name_hi',
    'relative_name': 'relative_name_hi',
    'rel_name': 'relative_name_hi',
    'संबंधी_का_नाम': 'relative_name_hi',
    'पिता_का_नाम': 'relative_name_hi',
    'पति_का_नाम': 'relative_name_hi',
    'पिता_पति_का_नाम': 'relative_name_hi',
    'अभिभावक_का_नाम': 'relative_name_hi',
    'रिश्तेदार_का_नाम': 'relative_name_hi',

    # Relation Type
    'relation_type': 'relation_type',
    'rel_type': 'relation_type',
    'relation': 'relation_type',
    'संबंध': 'relation_type',
    'संबंध_का_प्रकार': 'relation_type',
    'रिश्ता': 'relation_type',

    # House No
    'house_no': 'house_no',
    'houseno': 'house_no',
    'house_number': 'house_no',
    'house': 'house_no',
    'मकान': 'house_no',
    'मकान_नंबर': 'house_no',
    'मकान_संख्या': 'house_no',
    'मकान_नं': 'house_no',
    'गृह_संख्या': 'house_no',
    'घर_संख्या': 'house_no',

    # Age
    'age': 'age',
    'आयु': 'age',
    'उम्र': 'age',

    # Gender
    'gender': 'gender',
    'sex': 'gender',
    'लिंग': 'gender',
    'स्त्री_पुरुष': 'gender',

    # Section No
    'section_no': 'section_no',
    'section': 'section_no',
    'अनुभाग': 'section_no',
    'अनुभाग_संख्या': 'section_no',
    'अनुभाग_नं': 'section_no',

    # Mobile
    'mobile': 'mobile',
    'phone': 'mobile',
    'mobile_no': 'mobile',
    'phone_no': 'mobile',
    'contact': 'mobile',
    'contact_phone': 'mobile',
    'मोबाइल': 'mobile',
    'मोबाइल_नंबर': 'mobile',
    'मोबाइल_नं': 'mobile',
    'फ़ोन': 'mobile',
    'फोन': 'mobile',

    # Caste Tag
    'caste_tag': 'caste_tag',
    'caste': 'caste_tag',
    'community': 'caste_tag',
    'जाति': 'caste_tag',
    'जाति_टैग': 'caste_tag',
    'समुदाय': 'caste_tag'
}


def clean_header_key(h: str) -> str:
    """Normalizes raw header string by stripping punctuation, extra spaces and slashes."""
    if not h:
        return ""
    clean = re.sub(r'[\s/\\\-\.\(\)]+', '_', str(h).strip().lower()).strip('_')
    return clean


def normalize_gender(raw_val: Any) -> str:
    """Normalizes gender value to 'M', 'F', or 'O'."""
    if not raw_val:
        return 'O'
    s = str(raw_val).strip()
    upper = s.upper()
    if upper in ('M', 'MALE', 'MAN', 'BOY') or 'पुरुष' in s or s == 'पु':
        return 'M'
    elif upper in ('F', 'FEMALE', 'WOMAN', 'GIRL') or 'महिला' in s or 'स्त्री' in s or s == 'म':
        return 'F'
    elif upper in ('O', 'OTHER', 'OTHERS', 'TG', 'TRANSGENDER') or 'अन्य' in s:
        return 'O'
    first = upper[:1]
    if first == 'M':
        return 'M'
    elif first == 'F':
        return 'F'
    return 'O'


def normalize_relation_type(raw_val: Any) -> str:
    """Normalizes relationship type to 'F', 'H', 'M', or 'O'."""
    if not raw_val:
        return 'F'
    s = str(raw_val).strip()
    upper = s.upper()
    if 'पति' in s or upper.startswith('H') or 'HUSBAND' in upper:
        return 'H'
    elif 'माता' in s or upper.startswith('M') or 'MOTHER' in upper:
        return 'M'
    elif 'अन्य' in s or upper.startswith('O') or 'OTHER' in upper:
        return 'O'
    elif 'पिता' in s or upper.startswith('F') or 'FATHER' in upper:
        return 'F'
    return upper[:1] if upper else 'F'


def parse_int_safe(raw_val: Any, default: int = 0) -> int:
    """Safely extracts integer value from cell contents."""
    if raw_val is None:
        return default
    s = str(raw_val).strip()
    digits = re.findall(r'\d+', s)
    if digits:
        try:
            return int(digits[0])
        except Exception:
            return default
    return default


def normalize_mobile(raw_val: Any) -> Optional[str]:
    """Extracts 10-digit mobile number if possible."""
    if not raw_val:
        return None
    s = str(raw_val).strip()
    digits = re.sub(r'\D', '', s)
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    if len(digits) == 10:
        return digits
    return digits if digits else None


def generate_csv_template() -> str:
    """
    Generates standard voter roll CSV template with bilingual headers and 2 realistic sample rows.
    Header format:
    part_no,sl_no,epic_no,name_en,name_hi,relative_name_en,relative_name_hi,relation_type,house_no,age,gender,section_no,booth_no,mobile,caste_tag
    """
    headers = [
        "part_no", "sl_no", "epic_no", "name_en", "name_hi",
        "relative_name_en", "relative_name_hi", "relation_type",
        "house_no", "age", "gender", "section_no", "booth_no",
        "mobile", "caste_tag"
    ]
    rows = [
        [
            "1", "1", "RJ/01/001/000001", "Rajesh Kumar", "राजेश कुमार",
            "Rameshwar Lal", "रामेश्वर लाल", "F",
            "12-A", "42", "M", "1", "1",
            "9829012345", "Sharma"
        ],
        [
            "1", "2", "RJ/01/001/000002", "Sunita Devi", "सुनीता देवी",
            "Rajesh Kumar", "राजेश कुमार", "H",
            "12-A", "38", "F", "1", "1",
            "9829012346", "Sharma"
        ]
    ]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(headers)
    writer.writerows(rows)
    return out.getvalue()


def get_pg_connection():
    """Helper to connect to PostgreSQL 17 cluster."""
    try:
        import psycopg2
        conn = psycopg2.connect(
            dbname=os.environ.get('PGDATABASE', 'voter_platform'),
            user=os.environ.get('PGUSER', 'postgres'),
            password=os.environ.get('PGPASSWORD', 'postgres'),
            host=os.environ.get('PGHOST', 'localhost'),
            port=int(os.environ.get('PGPORT', 5432)),
            connect_timeout=3
        )
        return conn
    except Exception:
        return None


def _read_rows_from_file(file_obj_or_path: Any, filename: str) -> List[Dict[str, Any]]:
    """
    Extracts raw dictionaries from CSV or Excel file, auto-detecting encoding
    and normalizing column headers.
    """
    is_excel = filename.lower().endswith(('.xlsx', '.xls'))
    raw_rows: List[List[Any]] = []

    # Read binary bytes
    if isinstance(file_obj_or_path, bytes):
        file_bytes = file_obj_or_path
    elif isinstance(file_obj_or_path, str):
        if os.path.exists(file_obj_or_path):
            with open(file_obj_or_path, 'rb') as f:
                file_bytes = f.read()
        else:
            file_bytes = file_obj_or_path.encode('utf-8')
    elif hasattr(file_obj_or_path, 'read'):
        if hasattr(file_obj_or_path, 'seek'):
            try:
                file_obj_or_path.seek(0)
            except Exception:
                pass
        raw_read = file_obj_or_path.read()
        if isinstance(raw_read, bytes):
            file_bytes = raw_read
        else:
            file_bytes = str(raw_read).encode('utf-8')
    else:
        raise ValueError(f"Unsupported file object type: {type(file_obj_or_path)}")

    if is_excel:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
            sheet = wb.active
            for row in sheet.iter_rows(values_only=True):
                if any(v is not None for v in row):
                    raw_rows.append(list(row))
        except Exception as e:
            raise ValueError(f"Failed to read Excel file: {e}")
    else:
        # CSV reading with encoding fallbacks
        decoded_text = None
        for enc in ['utf-8-sig', 'utf-8', 'latin1', 'cp1252']:
            try:
                decoded_text = file_bytes.decode(enc)
                break
            except UnicodeDecodeError:
                continue

        if decoded_text is None:
            decoded_text = file_bytes.decode('utf-8', errors='replace')

        f_stream = io.StringIO(decoded_text)
        reader = csv.reader(f_stream)
        for r in reader:
            if any(v.strip() for v in r if isinstance(v, str)):
                raw_rows.append(r)

    if not raw_rows:
        return []

    # Map headers to canonical field names
    raw_headers = [str(c or '').strip() for c in raw_rows[0]]
    col_map: Dict[int, str] = {}
    for idx, h in enumerate(raw_headers):
        clean_k = clean_header_key(h)
        canonical = HEADER_MAPPING.get(clean_k)
        if not canonical:
            # Check partial match
            for k_pat, c_field in HEADER_MAPPING.items():
                if k_pat in clean_k:
                    canonical = c_field
                    break
        if canonical:
            col_map[idx] = canonical

    records: List[Dict[str, Any]] = []
    devanagari_pattern = re.compile(r'[\u0900-\u097F]')

    for row_idx, row in enumerate(raw_rows[1:], start=2):
        rec: Dict[str, Any] = {}
        for col_idx, val in enumerate(row):
            field = col_map.get(col_idx)
            if field:
                val_str = str(val).strip() if val is not None else ""
                rec[field] = val_str

        # If empty row, skip
        if not any(rec.values()):
            continue

        # Intelligent Devanagari detection for names
        name_en = rec.get('name_en', '')
        name_hi = rec.get('name_hi', '')
        if name_en and not name_hi:
            if devanagari_pattern.search(name_en):
                name_hi = name_en
                name_en = ""
        elif name_hi and not name_en:
            if not devanagari_pattern.search(name_hi):
                name_en = name_hi
                name_hi = ""

        # Default fallback so neither is completely null
        if not name_hi and name_en:
            name_hi = name_en
        if not name_en and name_hi:
            name_en = name_hi

        rec['name_en'] = name_en
        rec['name_hi'] = name_hi

        # Same for relative names
        rel_en = rec.get('relative_name_en', '')
        rel_hi = rec.get('relative_name_hi', '')
        if rel_en and not rel_hi:
            if devanagari_pattern.search(rel_en):
                rel_hi = rel_en
                rel_en = ""
        elif rel_hi and not rel_en:
            if not devanagari_pattern.search(rel_hi):
                rel_en = rel_hi
                rel_hi = ""
        if not rel_hi and rel_en:
            rel_hi = rel_en
        if not rel_en and rel_hi:
            rel_en = rel_hi

        rec['relative_name_en'] = rel_en
        rec['relative_name_hi'] = rel_hi

        # Normalize attributes
        part_no = parse_int_safe(rec.get('part_no'), default=1)
        sl_no = parse_int_safe(rec.get('sl_no'), default=row_idx - 1)
        age = parse_int_safe(rec.get('age'), default=18)
        gender = normalize_gender(rec.get('gender'))
        rel_type = normalize_relation_type(rec.get('relation_type'))
        mobile = normalize_mobile(rec.get('mobile'))
        caste_tag = str(rec.get('caste_tag', '')).strip()

        cleaned_rec = {
            'part_no': part_no,
            'sl_no': sl_no,
            'epic_no': rec.get('epic_no', ''),
            'name_en': rec.get('name_en', ''),
            'name_hi': rec.get('name_hi', ''),
            'name_norm': (rec.get('name_en') or rec.get('name_hi') or '').lower().strip(),
            'relative_name_en': rec.get('relative_name_en', ''),
            'relative_name_hi': rec.get('relative_name_hi', ''),
            'rel_norm': (rec.get('relative_name_en') or rec.get('relative_name_hi') or '').lower().strip(),
            'relation_type': rel_type,
            'house_no': str(rec.get('house_no', '')).strip(),
            'age': age,
            'gender': gender,
            'section_no': rec.get('section_no', ''),
            'contact_phone': mobile or '',
            'caste_tag': caste_tag,
            'source_file': filename
        }
        records.append(cleaned_rec)

    return records


def ingest_voter_file(
    file_obj_or_path: Any,
    filename: str,
    tenant_slug: str,
    storage_backend: Optional[str] = None
) -> Dict[str, Any]:
    """
    Ingests voter roll file (CSV or Excel) into the specified tenant's database partition.
    
    :param file_obj_or_path: Path to file or file-like object with .read()
    :param filename: Original filename for format & audit logging
    :param tenant_slug: Campaign tenant slug (e.g. 'ward82', 'jelatra')
    :param storage_backend: 'postgres' (primary; SQLite is permanently removed)
    :return: Summary dictionary {'success': True, 'total_rows': N, 'inserted': X, 'updated': Y, 'errors': [], 'sample': []}
    """
    slug = (tenant_slug or '').strip().lower()
    if not slug:
        raise ValueError("tenant_slug is required for voter roll ingestion")
    storage_backend = storage_backend or os.environ.get('STORAGE_BACKEND', 'postgres')
    if storage_backend in ('sqlite', 'sqlite3'):
        raise ValueError("Storage backend 'sqlite' is deprecated and removed. SmartBooth operates strictly on PostgreSQL.")

    records = _read_rows_from_file(file_obj_or_path, filename)
    total_rows = len(records)
    if total_rows == 0:
        return {
            'success': True,
            'total_rows': 0,
            'inserted': 0,
            'updated': 0,
            'errors': ['No voter records found in file'],
            'sample': []
        }

    pg_conn = get_pg_connection()
    if not pg_conn:
        raise RuntimeError("Unable to connect to PostgreSQL database for voter roll ingestion.")

    errors: List[str] = []
    inserted = 0
    updated = 0

    if pg_conn:
        try:
            with pg_conn.cursor() as cur:
                # 1. Resolve or create tenant_id
                cur.execute("SELECT tenant_id FROM tenants WHERE campaign_slug = %s", (slug,))
                row = cur.fetchone()
                if row:
                    tenant_id = row[0]
                else:
                    cur.execute("""
                        INSERT INTO tenants (campaign_slug, candidate_name, status)
                        VALUES (%s, %s, 'ACTIVE')
                        RETURNING tenant_id;
                    """, (slug, slug.title()))
                    tenant_id = cur.fetchone()[0]

                # 2. Ensure partition table exists
                partition_tbl = f"voters_t{tenant_id}"
                cur.execute(f"""
                    CREATE TABLE IF NOT EXISTS {partition_tbl}
                    PARTITION OF voters
                    FOR VALUES IN ({tenant_id});
                """)

                # 3. Ensure unique index on (tenant_id, part_no, sl_no)
                cur.execute(f"""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_{partition_tbl}_part_sl_unique
                    ON {partition_tbl} (tenant_id, part_no, sl_no);
                """)
                pg_conn.commit()

                # 4. Fetch existing (part_no, sl_no) keys for accurate insert/update metrics
                cur.execute(f"SELECT part_no, sl_no FROM {partition_tbl};")
                existing_keys = set(cur.fetchall())

                # 5. Chunked stream insert with ON CONFLICT DO UPDATE
                chunk_size = 500
                upsert_sql = f"""
                    INSERT INTO {partition_tbl} (
                        tenant_id, part_no, sl_no, epic_no, name_en, name_hi, name_norm,
                        rel_name_en, rel_name_hi, rel_norm, rel_type, house_no, age, gender,
                        contact_phone, caste_tag, source_file
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, part_no, sl_no) DO UPDATE SET
                        epic_no = COALESCE(NULLIF(EXCLUDED.epic_no, ''), {partition_tbl}.epic_no),
                        name_en = COALESCE(NULLIF(EXCLUDED.name_en, ''), {partition_tbl}.name_en),
                        name_hi = COALESCE(NULLIF(EXCLUDED.name_hi, ''), {partition_tbl}.name_hi),
                        name_norm = COALESCE(NULLIF(EXCLUDED.name_norm, ''), {partition_tbl}.name_norm),
                        rel_name_en = COALESCE(NULLIF(EXCLUDED.rel_name_en, ''), {partition_tbl}.rel_name_en),
                        rel_name_hi = COALESCE(NULLIF(EXCLUDED.rel_name_hi, ''), {partition_tbl}.rel_name_hi),
                        rel_norm = COALESCE(NULLIF(EXCLUDED.rel_norm, ''), {partition_tbl}.rel_norm),
                        rel_type = COALESCE(NULLIF(EXCLUDED.rel_type, ''), {partition_tbl}.rel_type),
                        house_no = COALESCE(NULLIF(EXCLUDED.house_no, ''), {partition_tbl}.house_no),
                        age = COALESCE(EXCLUDED.age, {partition_tbl}.age),
                        gender = COALESCE(NULLIF(EXCLUDED.gender, ''), {partition_tbl}.gender),
                        contact_phone = CASE 
                            WHEN EXCLUDED.contact_phone IS NOT NULL AND TRIM(EXCLUDED.contact_phone) != '' 
                            THEN EXCLUDED.contact_phone 
                            ELSE {partition_tbl}.contact_phone 
                        END,
                        caste_tag = CASE 
                            WHEN EXCLUDED.caste_tag IS NOT NULL AND TRIM(EXCLUDED.caste_tag) != '' 
                            THEN EXCLUDED.caste_tag 
                            ELSE {partition_tbl}.caste_tag 
                        END,
                        source_file = EXCLUDED.source_file;
                """

                for i in range(0, total_rows, chunk_size):
                    chunk = records[i:i + chunk_size]
                    batch_params = []
                    for r in chunk:
                        key = (r['part_no'], r['sl_no'])
                        if key in existing_keys:
                            updated += 1
                        else:
                            inserted += 1
                            existing_keys.add(key)

                        batch_params.append((
                            tenant_id, r['part_no'], r['sl_no'], r['epic_no'],
                            r['name_en'], r['name_hi'], r['name_norm'],
                            r['relative_name_en'], r['relative_name_hi'], r['rel_norm'],
                            r['relation_type'], r['house_no'], r['age'], r['gender'],
                            r['contact_phone'] or None, r['caste_tag'] or None, r['source_file']
                        ))

                    import psycopg2.extras
                    psycopg2.extras.execute_batch(cur, upsert_sql, batch_params)
                    pg_conn.commit()

                # Automated Universal Duplicate Detection Engine
                try:
                    from scripts.detect_duplicate_voters import detect_duplicates_in_campaign
                    detect_duplicates_in_campaign(cur, tenant_id, slug, apply=True)
                    pg_conn.commit()
                except Exception as de:
                    pass

            pg_conn.close()

        except Exception as e:
            if pg_conn:
                pg_conn.rollback()
                pg_conn.close()
            errors.append(f"PostgreSQL ingestion error: {e}")
            return {
                'success': False,
                'total_rows': total_rows,
                'inserted': inserted,
                'updated': updated,
                'errors': errors,
                'sample': records[:5]
            }

        return {
            'success': True,
            'total_rows': total_rows,
            'inserted': inserted,
            'updated': updated,
            'errors': errors,
            'sample': records[:5]
        }
