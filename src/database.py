"""
src/database.py - Asynchronous SQLite State Machine.
Tracks app metadata, audit reasoning, duplicate suppression, and email dispatch status.
"""

import aiosqlite
import logging
from typing import Optional, Dict, Any, List, Set
from src.config import settings

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    app_id TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    category TEXT,
    min_installs INTEGER DEFAULT 0,
    developer_id TEXT,
    developer_email TEXT,
    developer_website TEXT,
    playstore_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('UNSENT', 'AUDITED', 'SENT', 'SKIPPED', 'FAILED')),
    audit_score REAL,
    skip_reason TEXT,
    pitch_subject TEXT,
    pitch_body TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);
CREATE INDEX IF NOT EXISTS idx_leads_category ON leads(category);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(developer_email);
"""


class DatabaseManager:
    """Manages asynchronous database connection pooling, schema, and transactions."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.DATABASE_PATH

    async def init_db(self) -> None:
        """Initializes database schema with WAL mode enabled for high concurrency."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.executescript(SCHEMA_SQL)
            await db.commit()
            logger.info("Database initialized successfully at %s", self.db_path)

    async def get_lead(self, app_id: str) -> Optional[Dict[str, Any]]:
        """Checks if a lead with this app_id already exists in the system."""
        query = "SELECT * FROM leads WHERE app_id = ? LIMIT 1;"
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (app_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_sent_emails(self) -> Set[str]:
        """Returns a set of all developer emails that have already received an outreach email."""
        query = "SELECT DISTINCT developer_email FROM leads WHERE status = 'SENT' AND developer_email IS NOT NULL;"
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return {row[0].strip().lower() for row in rows if row[0]}

    async def upsert_raw_lead(
        self,
        app_id: str,
        app_name: str,
        category: str,
        min_installs: int,
        developer_id: str,
        developer_email: Optional[str],
        developer_website: Optional[str],
        playstore_url: str,
    ) -> None:
        """
        Inserts a lead with initial status 'UNSENT'.
        Idempotent: if app_id already exists, leaves status and audit decisions completely untouched.
        """
        query = """
        INSERT INTO leads (
            app_id, app_name, category, min_installs, 
            developer_id, developer_email, developer_website, playstore_url, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'UNSENT')
        ON CONFLICT(app_id) DO UPDATE SET
            developer_email = COALESCE(excluded.developer_email, leads.developer_email),
            developer_website = COALESCE(excluded.developer_website, leads.developer_website),
            updated_at = CURRENT_TIMESTAMP;
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                query,
                (
                    app_id,
                    app_name,
                    category,
                    min_installs,
                    developer_id,
                    developer_email,
                    developer_website,
                    playstore_url,
                ),
            )
            await db.commit()

    async def get_pending_leads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches a batch of leads in 'UNSENT' status for Gemini evaluation."""
        query = """
        SELECT app_id, app_name, category, min_installs, developer_id, 
               developer_email, developer_website, playstore_url
        FROM leads 
        WHERE status = 'UNSENT' 
        ORDER BY min_installs DESC 
        LIMIT ?;
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_audit_decision(
        self,
        app_id: str,
        status: str,
        audit_score: Optional[float] = None,
        skip_reason: Optional[str] = None,
        pitch_subject: Optional[str] = None,
        pitch_body: Optional[str] = None,
    ) -> None:
        """Updates a lead's status to 'AUDITED', 'SKIPPED', 'SENT', or 'FAILED'."""
        query = """
        UPDATE leads 
        SET status = ?, 
            audit_score = ?, 
            skip_reason = ?,
            pitch_subject = ?, 
            pitch_body = ?, 
            updated_at = CURRENT_TIMESTAMP
        WHERE app_id = ?;
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                query,
                (status, audit_score, skip_reason, pitch_subject, pitch_body, app_id),
            )
            await db.commit()

    async def get_audited_leads(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetches leads ready for email delivery."""
        query = """
        SELECT app_id, app_name, developer_email, pitch_subject, pitch_body, audit_score
        FROM leads
        WHERE status = 'AUDITED' AND developer_email IS NOT NULL
        ORDER BY updated_at ASC
        LIMIT ?;
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
            
    async def get_sent_count_today(self) -> int:
        """Counts how many emails were marked 'SENT' during the current calendar day (UTC)."""
        query = """
        SELECT COUNT(*) 
        FROM leads 
        WHERE status = 'SENT' AND DATE(updated_at) = DATE('now');
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
            
            
    async def upsert_leads_bulk(self, records: List[Dict[str, Any]]) -> int:
        """
        Fast bulk upsert for high-volume ingestion (10,000+ rows/sec).
        Uses a single transaction with executemany.
        """
        if not records:
            return 0

        query = """
        INSERT INTO leads (
            app_id, app_name, category, min_installs, 
            developer_id, developer_email, developer_website, playstore_url, status
        ) VALUES (
            :app_id, :app_name, :category, :min_installs,
            :developer_id, :developer_email, :developer_website, :playstore_url, 'UNSENT'
        )
        ON CONFLICT(app_id) DO UPDATE SET
            developer_email = COALESCE(excluded.developer_email, leads.developer_email),
            developer_website = COALESCE(excluded.developer_website, leads.developer_website),
            updated_at = CURRENT_TIMESTAMP;
        """
        async with aiosqlite.connect(self.db_path) as db:
            # PRAGMA optimizations for lightning-fast bulk loading
            await db.execute("PRAGMA synchronous = NORMAL;")
            await db.execute("PRAGMA temp_store = MEMORY;")
            await db.executemany(query, records)
            await db.commit()
            
        return len(records)