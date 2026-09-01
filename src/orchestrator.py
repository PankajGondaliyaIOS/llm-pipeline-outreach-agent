"""
src/orchestrator.py - Batch execution engine and state machine manager.
"""

import asyncio
import logging
from typing import Any, Dict, List

from src.config import settings
from src.database import DatabaseManager
from src.agent import OutreachAgent
from src.exceptions import LLMQuotaExhaustedError, LLMServiceUnavailableError
from src.schemas import OutreachDecision

logger = logging.getLogger(__name__)


class BatchOrchestrator:
    """Manages sequential audits with health-probe hibernation and state safety."""

    def __init__(self, db_manager: DatabaseManager, agent: OutreachAgent):
        self.db = db_manager
        self.agent = agent
        self.semaphore = asyncio.Semaphore(1)

    async def wait_for_quota_recovery(self) -> None:
        """Polls Gemini with a 1-token health ping until quota refills."""
        probe_interval = 900  # 15 minutes
        logger.info("[PROBE] Quota exhausted. Hibernating worker, checking every 15 minutes...")
        
        while True:
            await asyncio.sleep(probe_interval)
            print("[*] Probing Gemini API availability...")
            is_healthy = await self.agent.check_api_health()
            if is_healthy:
                print("[✓] Quota recovered! Resuming audits.")
                logger.info("[PROBE] Health check successful. Resuming batch processing.")
                break
            else:
                print("[⏳] Quota still exhausted. Retrying in 15 minutes...")

    async def process_lead(self, lead: Dict[str, Any]) -> None:
        """Audits an individual lead, ensuring zero data loss on upstream errors."""
        app_name = lead.get("app_name", "Unknown")
        app_id = lead.get("app_id")

        async with self.semaphore:
            try:
                print(f"[*] Auditing: {app_name}...")
                audit_result = await self.agent.audit_game(lead)

                if audit_result.decision == OutreachDecision.SEND:
                    new_status = "AUDITED"
                    skip_reason = None
                else:
                    new_status = "SKIPPED"
                    skip_reason = audit_result.reasoning

                await self.db.update_audit_decision(
                    app_id=app_id,
                    status=new_status,
                    audit_score=audit_result.audit_score,
                    skip_reason=skip_reason,
                    pitch_subject=audit_result.pitch_subject,
                    pitch_body=audit_result.pitch_body,
                )
                print(f"[✓] Completed: {app_name} -> {new_status} (Score: {audit_result.audit_score}/10)")

            except (LLMQuotaExhaustedError, LLMServiceUnavailableError) as err:
                # Do NOT mark lead FAILED. Keep it UNSENT in SQLite.
                print(f"[⏳ PAUSED] {err}. Keeping '{app_name}' as UNSENT.")
                logger.warning("%s on %s. Record preserved as UNSENT.", err.__class__.__name__, app_name)
                await self.wait_for_quota_recovery()
                return

            except Exception as e:
                # Permanent schema or payload error
                logger.error("Permanent error processing %s: %s", app_name, e)
                await self.db.update_audit_decision(
                    app_id=app_id,
                    status="FAILED",
                    audit_score=0.0,
                    skip_reason=str(e),
                )
                print(f"[✗] Failed: {app_name} -> Marked FAILED")

            await asyncio.sleep(6)  # Safe free-tier pace

    async def run_batch(self, limit: int = 5) -> None:
        """Fetches pending leads and processes them sequentially."""
        pending_leads = await self.db.get_pending_leads(limit=limit)

        if not pending_leads:
            print("[!] No 'UNSENT' leads found in database.")
            return

        print(f"[>] Found {len(pending_leads)} pending leads. Starting batch...")
        for lead in pending_leads:
            await self.process_lead(lead)
        print("[>] Batch execution completed.")