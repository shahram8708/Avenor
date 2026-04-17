import atexit
import json
import os
import socket
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from psycopg2 import pool
from psycopg2.extras import RealDictCursor
from psycopg2.extensions import connection as PgConnection
from werkzeug.security import check_password_hash, generate_password_hash

DEFAULT_DB_URL = os.getenv("DATABASE_URL", "").strip()


def _get_positive_int_from_env(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        parsed = int(raw_value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


DB_POOL_MIN_CONN = _get_positive_int_from_env("DB_POOL_MIN_CONN", 1)
DB_POOL_MAX_CONN = max(_get_positive_int_from_env("DB_POOL_MAX_CONN", 10), DB_POOL_MIN_CONN)

_POOL_LOCK = Lock()
_CONNECTION_POOLS: dict[str, pool.SimpleConnectionPool] = {}
_RESOLVED_DB_URLS: dict[str, str] = {}


CREATE_WAITLIST_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS waitlist_registrants (
    id            BIGSERIAL PRIMARY KEY,
    first_name    VARCHAR(100) NOT NULL,
    last_name     VARCHAR(100) NOT NULL,
    email         VARCHAR(320) NOT NULL UNIQUE,
    role          VARCHAR(120),
    industry      VARCHAR(120),
    ip_address    VARCHAR(128),
    user_agent    TEXT,
    referrer      TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    position      BIGSERIAL UNIQUE
);
"""

CREATE_PAGE_VISITS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS page_visits (
    id         BIGSERIAL PRIMARY KEY,
    ip_address VARCHAR(128),
    user_agent TEXT,
    referrer   TEXT,
    visited_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_ADMIN_SESSIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS admin_sessions (
    session_token VARCHAR(255) PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL
);
"""

CREATE_ROLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS roles (
    id          BIGSERIAL PRIMARY KEY,
    name        VARCHAR(64) NOT NULL UNIQUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

CREATE_ADMIN_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS admin_users (
    id            BIGSERIAL PRIMARY KEY,
    email         VARCHAR(320) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role_id       BIGINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _normalize_db_url(db_path: str | None = None) -> str:
    resolved = (db_path or DEFAULT_DB_URL).strip()
    if not resolved:
        raise RuntimeError("DATABASE_URL is required. Set it in your .env file.")
    if resolved.startswith("postgres://"):
        resolved = "postgresql://" + resolved[len("postgres://") :]
    return resolved


def _hostname_resolves_locally(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, None)
        return True
    except socket.gaierror:
        return False


def _extract_doh_answer_ip(payload: dict[str, Any], record_type: int) -> str | None:
    answers = payload.get("Answer")
    if not isinstance(answers, list):
        return None

    for answer in answers:
        if not isinstance(answer, dict):
            continue
        if int(answer.get("type", 0)) != record_type:
            continue
        ip_value = answer.get("data")
        if isinstance(ip_value, str) and ip_value.strip():
            return ip_value.strip()

    return None


def _resolve_hostname_via_doh(hostname: str) -> str | None:
    lookup_targets = [
        (
            f"https://dns.google/resolve?name={hostname}&type=A",
            1,
            {"Accept": "application/json"},
        ),
        (
            f"https://cloudflare-dns.com/dns-query?name={hostname}&type=A",
            1,
            {"Accept": "application/dns-json"},
        ),
        (
            f"https://dns.google/resolve?name={hostname}&type=AAAA",
            28,
            {"Accept": "application/json"},
        ),
        (
            f"https://cloudflare-dns.com/dns-query?name={hostname}&type=AAAA",
            28,
            {"Accept": "application/dns-json"},
        ),
    ]

    for query_url, record_type, headers in lookup_targets:
        request_headers = {
            "User-Agent": "avenor-db-resolver/1.0",
            **headers,
        }

        try:
            request = Request(query_url, headers=request_headers)
            with urlopen(request, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            continue

        if not isinstance(payload, dict):
            continue

        resolved_ip = _extract_doh_answer_ip(payload, record_type)
        if resolved_ip:
            return resolved_ip

    return None


def _inject_hostaddr_into_db_url(db_url: str, hostaddr: str) -> str:
    parsed = urlsplit(db_url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)

    for key, _ in query_items:
        if key.lower() == "hostaddr":
            return db_url

    query_items.append(("hostaddr", hostaddr))
    updated_query = urlencode(query_items, doseq=True)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, updated_query, parsed.fragment))


def _make_dns_resilient_db_url(db_url: str) -> str:
    hostname = urlsplit(db_url).hostname
    if not hostname:
        return db_url

    if _hostname_resolves_locally(hostname):
        return db_url

    resolved_ip = _resolve_hostname_via_doh(hostname)
    if not resolved_ip:
        raise RuntimeError(
            "DATABASE_URL host could not be resolved by local DNS. "
            "Set a DNS server like 1.1.1.1 or 8.8.8.8, or use a DATABASE_URL with a resolvable host."
        )

    warnings.warn(
        f"Local DNS could not resolve database host '{hostname}'. Using hostaddr fallback.",
        RuntimeWarning,
        stacklevel=2,
    )
    return _inject_hostaddr_into_db_url(db_url, resolved_ip)


def _get_pool(db_path: str | None = None) -> pool.SimpleConnectionPool:
    db_url = _normalize_db_url(db_path)

    connectable_db_url = _RESOLVED_DB_URLS.get(db_url)
    if connectable_db_url is None:
        connectable_db_url = _make_dns_resilient_db_url(db_url)
        _RESOLVED_DB_URLS[db_url] = connectable_db_url

    existing_pool = _CONNECTION_POOLS.get(connectable_db_url)
    if existing_pool is not None:
        return existing_pool

    with _POOL_LOCK:
        connectable_db_url = _RESOLVED_DB_URLS.get(db_url)
        if connectable_db_url is None:
            connectable_db_url = _make_dns_resilient_db_url(db_url)
            _RESOLVED_DB_URLS[db_url] = connectable_db_url

        existing_pool = _CONNECTION_POOLS.get(connectable_db_url)
        if existing_pool is not None:
            return existing_pool

        created_pool = pool.SimpleConnectionPool(
            minconn=DB_POOL_MIN_CONN,
            maxconn=DB_POOL_MAX_CONN,
            dsn=connectable_db_url,
            connect_timeout=10,
            application_name="avenor-prelaunch",
        )
        _CONNECTION_POOLS[connectable_db_url] = created_pool
        return created_pool


def _close_all_pools() -> None:
    with _POOL_LOCK:
        for conn_pool in _CONNECTION_POOLS.values():
            conn_pool.closeall()
        _CONNECTION_POOLS.clear()
        _RESOLVED_DB_URLS.clear()


atexit.register(_close_all_pools)


@contextmanager
def get_db_connection(db_path: str | None = None) -> Iterator[PgConnection]:
    conn_pool = _get_pool(db_path)
    connection = conn_pool.getconn()
    try:
        connection.autocommit = False
        yield connection
    finally:
        conn_pool.putconn(connection)


def _fetchone_dict(
    connection: PgConnection,
    query: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()
        return dict(row) if row else None


def _fetchall_dicts(
    connection: PgConnection,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def init_db(db_path: str | None = None) -> None:
    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(CREATE_WAITLIST_TABLE_SQL)
                cursor.execute(CREATE_PAGE_VISITS_TABLE_SQL)
                cursor.execute(CREATE_ADMIN_SESSIONS_TABLE_SQL)
                cursor.execute(CREATE_ROLES_TABLE_SQL)
                cursor.execute(CREATE_ADMIN_USERS_TABLE_SQL)
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_waitlist_registered_at ON waitlist_registrants(registered_at DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_users_email ON admin_users(LOWER(email))"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_users_role_id ON admin_users(role_id)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS idx_admin_sessions_expires_at ON admin_sessions(expires_at)"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def ensure_admin_role_and_user(
    admin_email: str,
    admin_password: str,
    db_path: str | None = None,
) -> None:
    email = (admin_email or "").strip().lower()
    password = admin_password or ""

    if not email:
        raise ValueError("Admin email is required for bootstrap.")
    if not password:
        raise ValueError("Admin password is required for bootstrap.")

    password_hash = generate_password_hash(password)

    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO roles (name, description)
                    VALUES (%s, %s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    ("admin", "System administrator with full dashboard access."),
                )

                cursor.execute("SELECT id FROM roles WHERE name = %s", ("admin",))
                role_row = cursor.fetchone()
                if not role_row:
                    raise RuntimeError("Unable to initialize admin role.")

                role_id = int(role_row["id"])
                cursor.execute(
                    "SELECT id FROM admin_users WHERE LOWER(email) = LOWER(%s)",
                    (email,),
                )
                existing_admin = cursor.fetchone()

                if existing_admin:
                    cursor.execute(
                        """
                        UPDATE admin_users
                        SET
                            email = %s,
                            password_hash = %s,
                            role_id = %s,
                            is_active = TRUE,
                            updated_at = NOW()
                        WHERE id = %s
                        """,
                        (email, password_hash, role_id, existing_admin["id"]),
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO admin_users (email, password_hash, role_id, is_active)
                        VALUES (%s, %s, %s, TRUE)
                        """,
                        (email, password_hash, role_id),
                    )

            conn.commit()
        except Exception:
            conn.rollback()
            raise


def authenticate_admin_user(
    admin_email: str,
    admin_password: str,
    db_path: str | None = None,
) -> dict[str, str] | None:
    email = (admin_email or "").strip().lower()
    password = admin_password or ""
    if not email or not password:
        return None

    with get_db_connection(db_path) as conn:
        row = _fetchone_dict(
            conn,
            """
            SELECT au.email, au.password_hash, r.name AS role_name
            FROM admin_users AS au
            INNER JOIN roles AS r ON r.id = au.role_id
            WHERE LOWER(au.email) = LOWER(%s)
              AND au.is_active = TRUE
              AND r.name = 'admin'
            LIMIT 1
            """,
            (email,),
        )

    if not row:
        return None
    if not check_password_hash(row["password_hash"], password):
        return None

    return {
        "email": row["email"],
        "role": row["role_name"],
    }


def record_page_visit(
    ip_address: str | None,
    user_agent: str | None,
    referrer: str | None,
    db_path: str | None = None,
) -> None:
    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO page_visits (ip_address, user_agent, referrer)
                    VALUES (%s, %s, %s)
                    """,
                    (ip_address, user_agent, referrer),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def add_waitlist_registrant(
    first_name: str,
    last_name: str,
    email: str,
    role: str | None,
    industry: str | None,
    ip_address: str | None,
    user_agent: str | None,
    referrer: str | None,
    db_path: str | None = None,
) -> dict[str, Any]:
    safe_email = (email or "").strip().lower()

    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(
                    """
                    INSERT INTO waitlist_registrants (
                        first_name,
                        last_name,
                        email,
                        role,
                        industry,
                        ip_address,
                        user_agent,
                        referrer
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                    RETURNING id, first_name, position
                    """,
                    (
                        first_name,
                        last_name,
                        safe_email,
                        role,
                        industry,
                        ip_address,
                        user_agent,
                        referrer,
                    ),
                )
                inserted = cursor.fetchone()

                if inserted:
                    conn.commit()
                    return {
                        "status": "created",
                        "id": inserted["id"],
                        "first_name": inserted["first_name"],
                        "position": inserted["position"],
                    }

                cursor.execute(
                    """
                    SELECT id, first_name, position
                    FROM waitlist_registrants
                    WHERE LOWER(email) = LOWER(%s)
                    LIMIT 1
                    """,
                    (safe_email,),
                )
                existing = cursor.fetchone()

                conn.commit()

                if existing:
                    return {
                        "status": "duplicate",
                        "id": existing["id"],
                        "first_name": existing["first_name"],
                        "position": existing["position"],
                    }

                raise RuntimeError("Unable to resolve waitlist insert result.")
        except Exception:
            conn.rollback()
            raise


def get_waitlist_count(db_path: str | None = None) -> int:
    with get_db_connection(db_path) as conn:
        row = _fetchone_dict(
            conn,
            "SELECT COUNT(*)::BIGINT AS total FROM waitlist_registrants",
        )
    return int(row["total"]) if row else 0


def get_registrant_by_id(registrant_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with get_db_connection(db_path) as conn:
        return _fetchone_dict(
            conn,
            """
            SELECT *
            FROM waitlist_registrants
            WHERE id = %s
            LIMIT 1
            """,
            (registrant_id,),
        )


def get_registrant_by_email(email: str, db_path: str | None = None) -> dict[str, Any] | None:
    with get_db_connection(db_path) as conn:
        return _fetchone_dict(
            conn,
            """
            SELECT *
            FROM waitlist_registrants
            WHERE LOWER(email) = LOWER(%s)
            LIMIT 1
            """,
            (email,),
        )


def create_admin_session(
    session_token: str,
    expires_at: datetime,
    db_path: str | None = None,
) -> None:
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO admin_sessions (session_token, expires_at)
                    VALUES (%s, %s)
                    ON CONFLICT (session_token)
                    DO UPDATE SET expires_at = EXCLUDED.expires_at
                    """,
                    (session_token, expires_at),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def is_admin_session_valid(session_token: str, db_path: str | None = None) -> bool:
    with get_db_connection(db_path) as conn:
        row = _fetchone_dict(
            conn,
            """
            SELECT session_token
            FROM admin_sessions
            WHERE session_token = %s
              AND expires_at > NOW()
            LIMIT 1
            """,
            (session_token,),
        )
    return row is not None


def delete_admin_session(session_token: str, db_path: str | None = None) -> None:
    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM admin_sessions WHERE session_token = %s",
                    (session_token,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def prune_expired_admin_sessions(db_path: str | None = None) -> None:
    with get_db_connection(db_path) as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM admin_sessions WHERE expires_at <= NOW()")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def get_dashboard_stats(db_path: str | None = None) -> dict[str, Any]:
    with get_db_connection(db_path) as conn:
        total_row = _fetchone_dict(
            conn,
            "SELECT COUNT(*)::BIGINT AS total FROM waitlist_registrants",
        )
        today_row = _fetchone_dict(
            conn,
            """
            SELECT COUNT(*)::BIGINT AS total
            FROM waitlist_registrants
            WHERE registered_at::date = CURRENT_DATE
            """,
        )
        week_row = _fetchone_dict(
            conn,
            """
            SELECT COUNT(*)::BIGINT AS total
            FROM waitlist_registrants
            WHERE registered_at::date >= CURRENT_DATE - INTERVAL '6 days'
            """,
        )

        popular_role_row = _fetchone_dict(
            conn,
            """
            SELECT role, COUNT(*)::BIGINT AS total
            FROM waitlist_registrants
            WHERE role IS NOT NULL AND BTRIM(role) <> ''
            GROUP BY role
            ORDER BY total DESC, role ASC
            LIMIT 1
            """,
        )

        popular_industry_row = _fetchone_dict(
            conn,
            """
            SELECT industry, COUNT(*)::BIGINT AS total
            FROM waitlist_registrants
            WHERE industry IS NOT NULL AND BTRIM(industry) <> ''
            GROUP BY industry
            ORDER BY total DESC, industry ASC
            LIMIT 1
            """,
        )

    return {
        "total": int(total_row["total"]) if total_row else 0,
        "today": int(today_row["total"]) if today_row else 0,
        "this_week": int(week_row["total"]) if week_row else 0,
        "popular_role": popular_role_row["role"] if popular_role_row else "N/A",
        "popular_industry": popular_industry_row["industry"] if popular_industry_row else "N/A",
    }


def get_registrants_paginated(
    page: int = 1,
    per_page: int = 50,
    sort_by: str = "date",
    db_path: str | None = None,
) -> dict[str, Any]:
    safe_page = max(page, 1)
    safe_per_page = max(per_page, 1)
    offset = (safe_page - 1) * safe_per_page

    query_by_sort = {
        "date": "ORDER BY registered_at DESC, id DESC",
        "name": "ORDER BY LOWER(first_name) ASC, LOWER(last_name) ASC, id DESC",
        "role": "ORDER BY role IS NULL, LOWER(role) ASC, LOWER(first_name) ASC, id DESC",
        "industry": "ORDER BY industry IS NULL, LOWER(industry) ASC, LOWER(first_name) ASC, id DESC",
    }

    selected_sort = sort_by if sort_by in query_by_sort else "date"
    order_clause = query_by_sort[selected_sort]

    with get_db_connection(db_path) as conn:
        total_row = _fetchone_dict(
            conn,
            "SELECT COUNT(*)::BIGINT AS total FROM waitlist_registrants",
        )
        total = int(total_row["total"]) if total_row else 0

        rows = _fetchall_dicts(
            conn,
            f"""
            SELECT
                id,
                first_name,
                last_name,
                email,
                role,
                industry,
                ip_address,
                user_agent,
                referrer,
                registered_at,
                position
            FROM waitlist_registrants
            {order_clause}
            LIMIT %s OFFSET %s
            """,
            (safe_per_page, offset),
        )

    total_pages = max((total + safe_per_page - 1) // safe_per_page, 1)

    return {
        "rows": rows,
        "page": safe_page,
        "per_page": safe_per_page,
        "total": total,
        "total_pages": total_pages,
        "sort_by": selected_sort,
    }


def get_registrations_per_day(last_days: int = 30, db_path: str | None = None) -> list[dict[str, Any]]:
    safe_days = max(last_days, 1)

    with get_db_connection(db_path) as conn:
        rows = _fetchall_dicts(
            conn,
            """
            WITH days AS (
                SELECT generate_series(
                    CURRENT_DATE - (%s::INT - 1),
                    CURRENT_DATE,
                    INTERVAL '1 day'
                )::DATE AS day
            )
            SELECT
                TO_CHAR(days.day, 'YYYY-MM-DD') AS day,
                COALESCE(COUNT(waitlist_registrants.id), 0)::BIGINT AS total
            FROM days
            LEFT JOIN waitlist_registrants
                ON waitlist_registrants.registered_at::DATE = days.day
            GROUP BY days.day
            ORDER BY days.day ASC
            """,
            (safe_days,),
        )

    return [{"day": row["day"], "total": int(row["total"])} for row in rows]


def get_distribution_by_field(field_name: str, db_path: str | None = None) -> list[dict[str, Any]]:
    safe_columns = {
        "role": "role",
        "industry": "industry",
    }
    selected_column = safe_columns.get(field_name)
    if not selected_column:
        return []

    query = f"""
        SELECT
            COALESCE(NULLIF(BTRIM({selected_column}), ''), 'Unspecified') AS label,
            COUNT(*)::BIGINT AS total
        FROM waitlist_registrants
        GROUP BY label
        ORDER BY total DESC, label ASC
    """

    with get_db_connection(db_path) as conn:
        rows = _fetchall_dicts(conn, query)

    return [{"label": row["label"], "total": int(row["total"])} for row in rows]


def get_all_registrants_for_csv(db_path: str | None = None) -> list[dict[str, Any]]:
    with get_db_connection(db_path) as conn:
        return _fetchall_dicts(
            conn,
            """
            SELECT
                id,
                first_name,
                last_name,
                email,
                role,
                industry,
                ip_address,
                user_agent,
                referrer,
                registered_at,
                position
            FROM waitlist_registrants
            ORDER BY registered_at DESC, id DESC
            """,
        )
