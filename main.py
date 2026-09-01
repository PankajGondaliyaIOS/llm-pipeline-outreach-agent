"""
main.py - Autonomous B2B Cold Outreach Agent & Production Daemon.
Provides a unified CLI for:
  - Lead Ingestion (--ingest <path>)
  - Headless Lead Audits (--audit-only)
  - Continuous 24/7 Outreach Daemon (--live or default simulation)
"""

import asyncio
import logging
import argparse
import signal
import sys
from pathlib import Path

from src.config import settings
from src.database import DatabaseManager
from src.agent import OutreachAgent
from src.dispatcher import EmailDispatcher
from src.orchestrator import BatchOrchestrator
from scripts.ingest_data import import_leads

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("outreach_worker")


class OutreachDaemon:
    """Coordinates the continuous audit and throttled delivery pipelines."""

    def __init__(self, dry_run: bool = True):
        self.db = DatabaseManager()
        self.agent = OutreachAgent()
        self.dry_run = dry_run
        self.dispatcher = EmailDispatcher(db_manager=self.db, dry_run=self.dry_run)
        self.orchestrator = BatchOrchestrator(db_manager=self.db, agent=self.agent)
        self.is_running = True

    def stop(self, *args):
        """Captures SIGINT/SIGTERM for clean shutdown without corrupting database transactions."""
        logger.info("Shutdown signal received. Terminating workers gracefully...")
        self.is_running = False

    async def _interruptible_sleep(self, seconds: int):
        """Sleeps in short intervals to remain responsive to SIGINT/SIGTERM."""
        for _ in range(seconds):
            if not self.is_running:
                break
            await asyncio.sleep(1)

    async def run_auditor_worker(self):
        """Worker 1: Evaluates UNSENT records via Gemini structured outputs."""
        logger.info("[AUDITOR WORKER] Active.")
        while self.is_running:
            try:
                pending_leads = await self.db.get_pending_leads(limit=5)
                if not pending_leads:
                    await self._interruptible_sleep(30)
                    continue

                sent_emails = await self.db.get_sent_emails()

                for lead in pending_leads:
                    if not self.is_running:
                        break

                    app_id = lead["app_id"]
                    developer_email = (lead.get("developer_email") or "").strip().lower()

                    # Deduplication guard: skip contacts already reached
                    if developer_email and developer_email in sent_emails:
                        logger.info("[AUDITOR] Skipping previously contacted recipient: %s", developer_email)
                        await self.db.update_audit_decision(
                            app_id=app_id,
                            status="SKIPPED",
                            audit_score=0.0,
                            skip_reason=f"Contact {developer_email} already received prior outreach.",
                        )
                        continue

                    await self.orchestrator.process_lead(lead)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[AUDITOR WORKER] Execution failure: %s", e, exc_info=True)
                await self._interruptible_sleep(15)

        logger.info("[AUDITOR WORKER] Stopped.")

    async def run_dispatcher_worker(self):
        """Worker 2: Delivers AUDITED leads respecting business hours and volume limits."""
        logger.info("[DISPATCHER WORKER] Active.")
        while self.is_running:
            try:
                # Enforce daytime business hours
                if not self.dispatcher.is_within_business_hours():
                    logger.info(
                        "[DISPATCHER] Outside business hours (%d:00-%d:00). Pausing 15 minutes...",
                        self.dispatcher.start_hour,
                        self.dispatcher.end_hour,
                    )
                    await self._interruptible_sleep(900)
                    continue

                # Enforce daily threshold
                sent_today = await self.db.get_sent_count_today()
                if sent_today >= self.dispatcher.daily_limit:
                    logger.info(
                        "[DISPATCHER] Daily limit reached (%d/%d sent). Pausing for 1 hour...",
                        sent_today,
                        self.dispatcher.daily_limit,
                    )
                    await self._interruptible_sleep(3600)
                    continue

                ready_leads = await self.db.get_audited_leads(limit=1)
                if not ready_leads:
                    await self._interruptible_sleep(30)
                    continue

                lead = ready_leads[0]
                app_id = lead.get("app_id")
                email = lead.get("developer_email")

                if not email:
                    await self.db.update_audit_decision(
                        app_id=app_id,
                        status="SKIPPED",
                        skip_reason="Missing developer email on dispatch.",
                    )
                    continue

                logger.info(
                    "[DISPATCHER] Delivering pitch: %s (%s) [Today: %d/%d]",
                    email,
                    lead.get("app_name"),
                    sent_today + 1,
                    self.dispatcher.daily_limit,
                )

                success = await self.dispatcher.send_pitch(lead)

                # Safeguard: if sending failed, mark FAILED so it never blocks the queue
                if not success:
                    await self.db.update_audit_decision(
                        app_id=app_id,
                        status="FAILED",
                        skip_reason="Delivery failed during dispatch worker execution.",
                    )

                if success and not self.dry_run:
                    await self.dispatcher.apply_throttle_delay()
                elif self.dry_run:
                    await asyncio.sleep(2)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[DISPATCHER WORKER] Execution failure: %s", e, exc_info=True)
                await self._interruptible_sleep(30)

        logger.info("[DISPATCHER WORKER] Stopped.")

    async def start(self):
        await self.db.init_db()
        mode_str = "DRY-RUN (Simulation)" if self.dry_run else "LIVE (Real SMTP Delivery)"
        logger.info("Initializing Agent Daemon in %s mode.", mode_str)

        await asyncio.gather(
            self.run_auditor_worker(),
            self.run_dispatcher_worker(),
        )


def main():
    parser = argparse.ArgumentParser(
        description="Autonomous Cold Outreach Agent with Gemini & SMTP Relay",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ingest",
        type=str,
        metavar="FILE_PATH",
        help="Path to CSV or Excel file containing target leads to import into SQLite",
    )
    parser.add_argument(
        "--audit-only",
        action="store_true",
        help="Process and qualify pending leads via Gemini without initiating email dispatch",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run daemon in LIVE delivery mode (defaults to dry-run preview if omitted)",
    )
    args = parser.parse_args()

    # Mode 1: Data Ingestion
    if args.ingest:
        if not Path(args.ingest).exists():
            print(f"[✗] Specified dataset file does not exist: {args.ingest}")
            sys.exit(1)
        asyncio.run(import_leads(args.ingest))
        return

    # Mode 2: Headless Audit
    if args.audit_only:
        db = DatabaseManager()
        agent = OutreachAgent()
        orchestrator = BatchOrchestrator(db_manager=db, agent=agent)

        async def run_audits():
            await db.init_db()
            await orchestrator.run_batch(limit=20)

        asyncio.run(run_audits())
        return

    # Mode 3: Continuous Daemon
    daemon = OutreachDaemon(dry_run=not args.live)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, daemon.stop)

    asyncio.run(daemon.start())


if __name__ == "__main__":
    main()