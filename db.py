import os
import sqlite3
import logging
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("smartbooth.db")

DATABASE_URL = os.environ.get('DATABASE_URL', '').strip()
PGHOST = os.environ.get("PGHOST", "localhost")
PGPORT = int(os.environ.get("PGPORT", 5432))
PGDATABASE = os.environ.get("PGDATABASE", "voter_platform")
PGUSER = os.environ.get("PGUSER", "postgres")
PGPASSWORD = os.environ.get("PGPASSWORD", "postgres")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Always default to PostgreSQL
IS_POSTGRES = True

DB_MINCONN = int(os.environ.get("DB_MINCONN", 1))
DB_MAXCONN = int(os.environ.get("DB_MAXCONN", 5))

try:
    import psycopg2
    from psycopg2 import pool
    
    if DATABASE_URL and DATABASE_URL.startswith(('postgres://', 'postgresql://')):
        pg_url = DATABASE_URL
        if pg_url.startswith("postgres://"):
            pg_url = pg_url.replace("postgres://", "postgresql://", 1)
        pg_pool = psycopg2.pool.ThreadedConnectionPool(minconn=DB_MINCONN, maxconn=DB_MAXCONN, dsn=pg_url)
    else:
        pg_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=DB_MINCONN,
            maxconn=DB_MAXCONN,
            host=PGHOST,
            port=PGPORT,
            dbname=PGDATABASE,
            user=PGUSER,
            password=PGPASSWORD
        )
    logger.info("Connected to PostgreSQL multi-tenant connection pool (min=%d, max=%d)", DB_MINCONN, DB_MAXCONN)
except Exception as e:
    pg_pool = None
    IS_POSTGRES = False
    logger.warning("PostgreSQL pool initialization failed (%s), legacy SQLite fallback enabled", e)


def get_db():
    """
    Returns a database connection and a helper flag.
    For Postgres: retrieves a connection from the pool.
    For SQLite: opens connection with WAL mode enabled to prevent write-locks.
    """
    if IS_POSTGRES:
        conn = pg_pool.getconn()
        return conn, True
    else:
        conn = sqlite3.connect(SQLITE_PATH, timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Enable WAL mode & 10s timeout to completely eliminate 'database is locked' errors
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn, False


def release_db(conn, is_pg=False):
    """Releases a connection back to the pool or closes SQLite connection."""
    if is_pg and pg_pool:
        try:
            pg_pool.putconn(conn)
        except Exception:
            pass
    elif conn:
        try:
            conn.close()
        except Exception:
            pass


def execute_query(sql, params=(), fetch_one=False, fetch_all=True, commit=False):
    """
    Unified query executor supporting both PostgreSQL and SQLite.
    Automatically handles parameter placeholders (? vs %s) and connection lifecycles.
    """
    conn, is_pg = get_db()
    cursor = conn.cursor()
    try:
        # Convert parameter syntax if needed
        query = sql
        if is_pg:
            # Replace SQLite ? with %s for Postgres
            query = query.replace('?', '%s')
        else:
            # Replace %s with ? for SQLite
            query = query.replace('%s', '?')

        cursor.execute(query, params)

        if commit:
            conn.commit()

        if fetch_one:
            row = cursor.fetchone()
            if row is None:
                return None
            if is_pg:
                colnames = [desc[0] for desc in cursor.description]
                return dict(zip(colnames, row))
            else:
                return dict(row)

        if fetch_all:
            rows = cursor.fetchall()
            if is_pg:
                colnames = [desc[0] for desc in cursor.description]
                return [dict(zip(colnames, r)) for r in rows]
            else:
                return [dict(r) for r in rows]

        return cursor.rowcount
    finally:
        cursor.close()
        release_db(conn, is_pg)
