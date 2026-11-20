"""
init_test_postgres.py
====================
Deterministic PostgreSQL 17 test database initializer for SmartBooth.
Used both locally and in GitHub Actions CI for 100% PostgreSQL test parity.

SAFETY: this script deletes voter rows and resets quota balances for tenants 1
and 3. Those are live campaigns (jelatra, ward82) in production, and the
production unit file (smartbooth.service) exports PGDATABASE=voter_platform.
So the target database name is checked before anything is written: it must end
in '_test' or '_ci'. Override only with TEST_DB_ALLOW_UNSAFE=1, and only when
you genuinely mean to rewrite that database.
"""

import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

PLATFORM_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)

from tests.seed_electors import TENANTS, ELECTORS, as_dict

DB_HOST = os.environ.get('PGHOST', 'localhost')
DB_PORT = int(os.environ.get('PGPORT', 5432))
DB_USER = os.environ.get('PGUSER', 'postgres')
DB_PASS = os.environ.get('PGPASSWORD', 'postgres')
# Must match the default in the test modules (tests/test_audit_log.py et al) so
# that an unset PGDATABASE seeds and reads the same database.
TEST_DB_NAME = os.environ.get('PGDATABASE', 'voter_platform_test')

SAFE_DB_SUFFIXES = ('_test', '_ci')

# Tenants and electors come from tests/seed_electors.py for PostgreSQL test database seeding.
SEED_TENANT_IDS = tuple(t[0] for t in TENANTS)
SEED_QUOTA_CREDITS = 100


def assert_safe_target(db_name):
    """Refuses to touch anything that is not obviously a throwaway test database."""
    if os.environ.get('TEST_DB_ALLOW_UNSAFE') == '1':
        print(f"WARNING: TEST_DB_ALLOW_UNSAFE=1 -- rewriting '{db_name}' without the name check.")
        return
    if not db_name.endswith(SAFE_DB_SUFFIXES):
        tenants = ', '.join(str(t) for t in SEED_TENANT_IDS)
        sys.exit(
            f"REFUSING to initialize '{db_name}'.\n"
            f"This script deletes voters and resets quota for tenants {tenants}, which are\n"
            f"live campaigns in production. Point PGDATABASE at a database whose name ends\n"
            f"in {' or '.join(SAFE_DB_SUFFIXES)} (e.g. PGDATABASE=voter_platform_test), or set\n"
            f"TEST_DB_ALLOW_UNSAFE=1 if you really mean to rewrite '{db_name}'."
        )


def get_maintenance_connection():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname='postgres'
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def get_test_db_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        dbname=TEST_DB_NAME
    )


def create_test_database_if_needed():
    conn = get_maintenance_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
        if not cur.fetchone():
            print(f"Creating database '{TEST_DB_NAME}'...")
            cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
        else:
            print(f"Database '{TEST_DB_NAME}' already exists.")
    conn.close()


def apply_migrations():
    print(f"Applying PostgreSQL schema to '{TEST_DB_NAME}'...")
    conn = get_test_db_connection()
    conn.autocommit = True
    cur = conn.cursor()

    # 1. Enable extensions
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2. Apply SQL DDL files from migrations/postgres.
    #    psycopg2 sends a multi-statement string as one implicit transaction, so a
    #    single bad statement discards the entire file. Never swallow that: testing
    #    against a silently half-applied schema is worse than not testing at all.
    #    A missing file is an error too, not a skip.
    migrations_dir = os.path.join(PLATFORM_ROOT, 'migrations', 'postgres')
    sql_files = [
        '0001_master_partitioned_schema.sql',
        '0002_dispatch_history.sql',
        '0003_trgm_and_perf_indexes.sql',
        '0004_whatsapp_quota.sql',
        '0005_audit_log.sql',
        '0006_family_source_type.sql',
        '0007_outside_voters.sql',
        '0008_fffd_scan_log.sql',
        '0009_phone_source_and_gram_panchayat.sql',
        '0010_tenant_total_voters.sql'
    ]

    for fname in sql_files:
        fpath = os.path.join(migrations_dir, fname)
        if not os.path.exists(fpath):
            raise FileNotFoundError(
                f"Migration '{fname}' is listed in sql_files but missing from {migrations_dir}. "
                f"Was it renamed? Update the sql_files list."
            )
        with open(fpath, 'r', encoding='utf-8') as f:
            sql = f.read()
        cur.execute(sql)
        print(f"  Applied {fname}")

    # 3. Ensure tenants exist
    for tenant_id, slug, candidate_name, state, election_type in TENANTS:
        cur.execute("""
            INSERT INTO tenants (tenant_id, campaign_slug, candidate_name, state, election_type, status)
            VALUES (%s, %s, %s, %s, %s, 'ACTIVE')
            ON CONFLICT (campaign_slug) DO UPDATE
            SET candidate_name = EXCLUDED.candidate_name, status = 'ACTIVE';
        """, (tenant_id, slug, candidate_name, state, election_type))

    # 4. Ensure tenant partitions exist. Partition names are built from the
    #    tenant ids in seed_electors.py (ints, not user input), matching the
    #    voters_t<id> convention in campaign_manager.create_campaign.
    for tenant_id, _slug, _name, _state, _etype in TENANTS:
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS voters_t{int(tenant_id)} "
            f"PARTITION OF voters FOR VALUES IN ({int(tenant_id)});"
        )

    # 5. Clear and seed deterministic test voters from the shared fixture
    cur.execute("DELETE FROM voters WHERE tenant_id = ANY(%s);", (list(SEED_TENANT_IDS),))

    insert_sql = """
        INSERT INTO voters (
            voter_id, tenant_id, part_no, sl_no, epic_no,
            name_en, name_hi, rel_name_en, rel_name_hi, rel_type,
            age, gender, house_no, booth_name,
            contact_phone, caste_tag, family_id, family_count, is_family_head,
            is_voted, is_dispatched, status
        ) VALUES (
            %(voter_id)s, %(tenant_id)s, %(part_no)s, %(sl_no)s, %(epic_no)s,
            %(name_en)s, %(name_hi)s, %(rel_name_en)s, %(rel_name_hi)s, %(rel_type)s,
            %(age)s, %(gender)s, %(house_no)s, %(booth_name)s,
            %(contact_phone)s, %(caste_tag)s, %(family_id)s, %(family_count)s, %(is_family_head)s,
            %(is_voted)s, %(is_dispatched)s, 'ACTIVE'
        );
    """
    for tenant_id, _slug, _name, _state, _etype in TENANTS:
        rows = [as_dict(r) for r in ELECTORS[tenant_id]]
        sizes = {}
        for e in rows:
            sizes[e['family_id']] = sizes.get(e['family_id'], 0) + 1
        seen = set()
        for e in rows:
            fam = e['family_id']
            e = dict(e, tenant_id=tenant_id, family_count=sizes[fam], is_family_head=fam not in seen)
            seen.add(fam)
            # search_doc is filled by the trg_voters_search_doc trigger, which
            # fires on partition inserts too -- do not set it here.
            cur.execute(insert_sql, e)

    # 6. Advance the SERIAL sequences past the ids seeded explicitly above.
    #    Every campaign-creation path (campaign_manager.create_campaign,
    #    voter_ingest, scripts/provision_campaign.py, scripts/client_provisioner.py)
    #    inserts into tenants WITHOUT a tenant_id and relies on nextval. Seeding
    #    tenant_id 1 and 3 by hand leaves the sequence at 1, so the first campaign
    #    a test creates dies on 'duplicate key value violates tenants_pkey'.
    cur.execute("""
        SELECT setval(
            pg_get_serial_sequence('tenants', 'tenant_id'),
            GREATEST((SELECT COALESCE(MAX(tenant_id), 0) FROM tenants), 1)
        );
    """)
    cur.execute("""
        SELECT setval(
            pg_get_serial_sequence('voters', 'voter_id'),
            GREATEST((SELECT COALESCE(MAX(voter_id), 0) FROM voters), 1)
        );
    """)

    # 7. Reset the mutable audit/quota state so repeated local runs start identical.
    #    The quota tests spend credits and call set_granted; the audit tests write
    #    rows. Without this, run N+1 inherits run N's drained balance and residue.
    cur.execute("TRUNCATE whatsapp_quota_ledger, whatsapp_quota_reservation;")
    cur.execute("TRUNCATE audit_log, voting_audit_log, dispatch_log, dispatch_history;")

    # 8. Seed the WhatsApp quota to a known balance
    cur.execute("""
        INSERT INTO whatsapp_quota (tenant_id, granted, consumed, reserved, is_blocked)
        VALUES
            (1, %(credits)s, 0, 0, FALSE),
            (3, %(credits)s, 0, 0, FALSE)
        ON CONFLICT (tenant_id) DO UPDATE
        SET granted = EXCLUDED.granted,
            consumed = 0,
            reserved = 0,
            is_blocked = FALSE;
    """, {'credits': SEED_QUOTA_CREDITS})

    cur.close()
    conn.close()

    import user_manager
    user_manager.ensure_schema()

    print(f"Successfully initialized '{TEST_DB_NAME}' with test tenants, schema, and seed electors.")


if __name__ == '__main__':
    assert_safe_target(TEST_DB_NAME)
    print(f"Target database: {TEST_DB_NAME} @ {DB_HOST}:{DB_PORT} (user {DB_USER})")
    create_test_database_if_needed()
    apply_migrations()
