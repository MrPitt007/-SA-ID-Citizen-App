"""
SA-ID Citizen App — PostgreSQL Audit Database
Creates tables and handles all audit log writes/reads.
Run setup_database() once to create tables.
"""
import hashlib
import time
import json
import re
from typing import Optional

# ── Try importing psycopg2, fall back to simulation ──────────────────────────
try:
    import psycopg2
    import psycopg2.extras
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    print("[SA-ID DB] psycopg2 not installed — running in simulation mode")
    print("[SA-ID DB] Install with: pip install psycopg2-binary")

# ── Database connection config ─────────────────────────────────────────────────
# Copy .env.example to .env and fill in your PostgreSQL details
DB_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "database": "said_citizen",
    "user":     "said_user",
    "password": "CHANGE_ME_IN_PRODUCTION",
}

# ── SQL to create all tables ───────────────────────────────────────────────────
CREATE_TABLES_SQL = """

-- Audit log — every API call is recorded here
CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL PRIMARY KEY,
    event_type      VARCHAR(64)  NOT NULL,
    id_number_hash  VARCHAR(64)  NOT NULL,
    endpoint        VARCHAR(128) NOT NULL,
    success         BOOLEAN      NOT NULL,
    ip_address      VARCHAR(45),
    session_token   VARCHAR(128),
    result_summary  TEXT,
    error_message   TEXT,
    duration_ms     FLOAT,
    popia_compliant BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Signed documents — every document signed is stored here
CREATE TABLE IF NOT EXISTS signed_documents (
    id                  SERIAL PRIMARY KEY,
    signature_reference VARCHAR(32)  NOT NULL UNIQUE,
    reference_number    VARCHAR(64),
    document_type       VARCHAR(32)  NOT NULL,
    document_title      VARCHAR(128) NOT NULL,
    requesting_party    VARCHAR(128) NOT NULL,
    signer_id_hash      VARCHAR(64)  NOT NULL,
    document_hash       VARCHAR(64)  NOT NULL,
    block_hash          VARCHAR(64),
    legal_status        VARCHAR(32)  DEFAULT 'LEGALLY_BINDING',
    popia_compliant     BOOLEAN      DEFAULT TRUE,
    signed_at           TIMESTAMPTZ  DEFAULT NOW()
);

-- Citizen sessions — every login session
CREATE TABLE IF NOT EXISTS citizen_sessions (
    id              SERIAL PRIMARY KEY,
    session_token   VARCHAR(128) NOT NULL UNIQUE,
    id_number_hash  VARCHAR(64)  NOT NULL,
    auth_method     VARCHAR(32)  NOT NULL,
    biometric_score FLOAT,
    liveness        VARCHAR(16),
    ip_address      VARCHAR(45),
    active          BOOLEAN      DEFAULT TRUE,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  DEFAULT NOW() + INTERVAL '30 minutes'
);

-- Grant checks — every SASSA query
CREATE TABLE IF NOT EXISTS grant_checks (
    id              SERIAL PRIMARY KEY,
    id_number_hash  VARCHAR(64)  NOT NULL,
    grant_type      VARCHAR(32)  NOT NULL,
    grant_status    VARCHAR(16)  NOT NULL,
    monthly_amount  FLOAT,
    currency        VARCHAR(8)   DEFAULT 'ZAR',
    checked_at      TIMESTAMPTZ  DEFAULT NOW()
);

-- Notification log — every SMS/email/push sent
CREATE TABLE IF NOT EXISTS notification_logs (
    id                  SERIAL PRIMARY KEY,
    notification_id     VARCHAR(32)  NOT NULL UNIQUE,
    notification_type   VARCHAR(32)  NOT NULL,
    channel             VARCHAR(16)  NOT NULL,
    recipient_masked    VARCHAR(32),
    status              VARCHAR(16)  DEFAULT 'DELIVERED',
    reference           VARCHAR(64),
    sent_at             TIMESTAMPTZ  DEFAULT NOW()
);

-- Create indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_audit_id_hash    ON audit_logs(id_number_hash);
CREATE INDEX IF NOT EXISTS idx_audit_event      ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_created    ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_docs_signer      ON signed_documents(signer_id_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_token   ON citizen_sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_sessions_id_hash ON citizen_sessions(id_number_hash);
"""

# ── Connect to database ───────────────────────────────────────────────────────
def get_connection():
    if not DB_AVAILABLE:
        return None
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"[SA-ID DB] Connection failed: {e}")
        return None

# ── Setup — run once ──────────────────────────────────────────────────────────
def setup_database() -> dict:
    """Creates all tables. Run once on first startup."""
    conn = get_connection()
    if not conn:
        return {"success": False, "error": "Database not available", "mode": "simulation"}
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLES_SQL)
        conn.commit()
        conn.close()
        return {"success": True, "message": "All tables created successfully"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Write audit log ───────────────────────────────────────────────────────────
def write_audit_log(
    event_type:     str,
    id_number_hash: str,
    endpoint:       str,
    success:        bool,
    ip_address:     Optional[str] = None,
    session_token:  Optional[str] = None,
    result_summary: Optional[str] = None,
    error_message:  Optional[str] = None,
    duration_ms:    Optional[float] = None,
) -> dict:
    conn = get_connection()
    if not conn:
        # Simulation mode — just return success
        return {
            "success": True,
            "mode": "simulation",
            "event_type": event_type,
            "timestamp": int(time.time()),
        }
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit_logs
                (event_type, id_number_hash, endpoint, success, ip_address,
                 session_token, result_summary, error_message, duration_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id
            """, (event_type, id_number_hash, endpoint, success, ip_address,
                  session_token, result_summary, error_message, duration_ms))
            log_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return {"success": True, "log_id": log_id, "event_type": event_type}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Write signed document ─────────────────────────────────────────────────────
def write_signed_document(sign_result: dict) -> dict:
    conn = get_connection()
    if not conn:
        return {"success": True, "mode": "simulation"}
    try:
        ab = sign_result.get("audit_block", {})
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO signed_documents
                (signature_reference, reference_number, document_type, document_title,
                 requesting_party, signer_id_hash, document_hash, block_hash, legal_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (signature_reference) DO NOTHING
                RETURNING id
            """, (
                sign_result.get("signature_reference"),
                sign_result.get("reference_number"),
                sign_result.get("document_type"),
                sign_result.get("document_title"),
                sign_result.get("requesting_party"),
                sign_result.get("signer_id_hash"),
                sign_result.get("document_hash"),
                ab.get("block_hash"),
                sign_result.get("legal_status"),
            ))
        conn.commit()
        conn.close()
        return {"success": True, "stored": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Write session ─────────────────────────────────────────────────────────────
def write_session(auth_result: dict, ip_address: Optional[str] = None) -> dict:
    conn = get_connection()
    if not conn:
        return {"success": True, "mode": "simulation"}
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO citizen_sessions
                (session_token, id_number_hash, auth_method, biometric_score, liveness, ip_address)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (session_token) DO NOTHING
            """, (
                auth_result.get("session_token"),
                auth_result.get("id_number_hash"),
                auth_result.get("auth_method"),
                auth_result.get("biometric_score"),
                auth_result.get("liveness"),
                ip_address,
            ))
        conn.commit()
        conn.close()
        return {"success": True, "stored": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Read audit logs ───────────────────────────────────────────────────────────
def get_audit_logs(id_number_hash: Optional[str] = None, limit: int = 50) -> dict:
    conn = get_connection()
    if not conn:
        return {"success": True, "mode": "simulation", "logs": [], "total": 0}
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if id_number_hash:
                cur.execute("""
                    SELECT * FROM audit_logs
                    WHERE id_number_hash = %s
                    ORDER BY created_at DESC LIMIT %s
                """, (id_number_hash, limit))
            else:
                cur.execute("""
                    SELECT * FROM audit_logs
                    ORDER BY created_at DESC LIMIT %s
                """, (limit,))
            logs = [dict(row) for row in cur.fetchall()]
        conn.close()
        return {"success": True, "logs": logs, "total": len(logs)}
    except Exception as e:
        return {"success": False, "error": str(e)}

# ── Dashboard stats ───────────────────────────────────────────────────────────
def get_dashboard_stats() -> dict:
    conn = get_connection()
    if not conn:
        # Return simulation stats for dashboard
        return {
            "success": True,
            "mode": "simulation",
            "total_verifications": 1247,
            "total_documents_signed": 384,
            "total_sessions": 2891,
            "total_grant_checks": 756,
            "success_rate": 98.4,
            "timestamp": int(time.time()),
        }
    try:
        stats = {}
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM audit_logs")
            stats["total_audit_logs"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM audit_logs WHERE success = TRUE")
            stats["total_success"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM signed_documents")
            stats["total_documents_signed"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM citizen_sessions")
            stats["total_sessions"] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM grant_checks")
            stats["total_grant_checks"] = cur.fetchone()[0]
        conn.close()
        total = stats["total_audit_logs"]
        stats["success_rate"] = round(
            (stats["total_success"] / total * 100) if total > 0 else 0, 1
        )
        stats["success"] = True
        stats["timestamp"] = int(time.time())
        return stats
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    print("Setting up SA-ID Citizen database...")
    result = setup_database()
    print(result)
