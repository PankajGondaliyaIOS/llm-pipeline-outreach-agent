"""
src/dispatcher.py - Asynchronous Email Dispatcher using aiosmtplib.
Handles TLS transmission, Dry-Run simulation, state transitions,
business hours scheduling, and randomized safety throttling.
"""

import re
import ssl
import certifi
import asyncio
import random
import logging
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Dict, Any, Optional

import aiosmtplib

from src.config import settings
from src.database import DatabaseManager

logger = logging.getLogger(__name__)


class EmailDispatcher:
    """Dispatches emails asynchronously and records state transitions with safety controls."""

    def __init__(self, db_manager: DatabaseManager, dry_run: bool = True):
        self.db = db_manager
        self.dry_run = dry_run

        # Safety & throttle limits from settings
        self.delay_min = getattr(settings, "DELAY_MIN", 480)
        self.delay_max = getattr(settings, "DELAY_MAX", 720)
        self.daily_limit = getattr(settings, "DAILY_LIMIT", 50)
        self.start_hour = getattr(settings, "WORK_HOURS_START", 9)
        self.end_hour = getattr(settings, "WORK_HOURS_END", 17)

    def is_within_business_hours(self) -> bool:
        """Verifies dispatch occurs strictly during daytime business hours."""
        current_hour = datetime.now().hour
        return self.start_hour <= current_hour < self.end_hour

    async def apply_throttle_delay(self) -> None:
        """Suspends execution for a randomized jitter delay between 8 to 12 minutes."""
        delay = random.uniform(self.delay_min, self.delay_max)
        minutes = delay / 60
        print(f"[⏳ THROTTLE] Pausing for {minutes:.1f} minutes ({delay:.0f}s) to safeguard sender score...")
        await asyncio.sleep(delay)

    def build_message(self, recipient: str, subject: str, body: str) -> EmailMessage:
        """Constructs a compliant RFC MIME message with correct headers and ZOGOEX signature."""
        clean_body = re.sub(
            r"(?i)\n*(best|regards|best regards|cheers|sincerely|thanks),?\s*$",
            "",
            body.strip(),
        )

        footer = (
            "\n\n---\n"
            "Best regards,\n"
            f"{settings.SMTP_FROM_NAME}\n"
            "Founder & Director, ZOGOEX LTD\n"
            "https://zogoex.com"
        )
        full_body = clean_body + footer

        msg = EmailMessage()
        msg["From"] = f"{settings.SMTP_FROM_NAME} <{settings.SMTP_FROM_EMAIL}>"
        msg["To"] = recipient
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="zogoex.com")
        msg.set_content(full_body)
        return msg

    async def send_via_smtp(self, lead: dict) -> bool:
        """Transmits raw MIME message via Google Workspace Relay with certifi TLS."""
        recipient = lead.get("developer_email")
        subject = lead.get("pitch_subject", "")
        body = lead.get("pitch_body", "")

        if not recipient:
            logger.error("send_via_smtp called without developer_email.")
            return False

        message = self.build_message(recipient, subject, body)
        tls_context = ssl.create_default_context(cafile=certifi.where())

        username = settings.SMTP_USER if settings.SMTP_USER else None
        password = settings.SMTP_PASSWORD if settings.SMTP_PASSWORD else None

        try:
            await aiosmtplib.send(
                message,
                hostname=settings.SMTP_HOST,
                port=settings.SMTP_PORT,
                username=username,
                password=password,
                use_tls=(settings.SMTP_PORT == 465),
                start_tls=(settings.SMTP_PORT == 587),
                tls_context=tls_context,
                recipients=[recipient],
            )
            return True
        except Exception as e:
            logger.error("Failed SMTP delivery to %s: %s", recipient, e)
            raise e

    async def send_pitch(self, lead: Dict[str, Any]) -> bool:
        """Dispatches an email or runs dry-run, then updates DB status to SENT or FAILED."""
        app_id = lead["app_id"]
        recipient = lead.get("developer_email")
        subject = lead.get("pitch_subject")
        body = lead.get("pitch_body")

        if not recipient:
            logger.warning("No recipient email found for %s, skipping.", app_id)
            return False

        if self.dry_run:
            msg = self.build_message(recipient=recipient, subject=subject, body=body)
            print("\n" + "=" * 60)
            print("[DRY-RUN EMAIL]")
            print(f"From:    {msg['From']}")
            print(f"To:      {msg['To']}")
            print(f"Subject: {msg['Subject']}")
            print("-" * 60)
            print(msg.get_content())
            print("=" * 60 + "\n")
        else:
            try:
                # Correct call: pass lead dictionary
                await self.send_via_smtp(lead)
                print(f"[✓ LIVE SENT] Dispatched to: {recipient} ({lead.get('app_name')})")
            except Exception as e:
                logger.error("Failed to deliver email to %s: %s", recipient, e)
                print(f"[✗ ERROR] Failed sending to {recipient}: {e}")
                await self.db.update_audit_decision(
                    app_id=app_id,
                    status="FAILED",
                    skip_reason=str(e),
                )
                return False

        # Transition state machine: mark as SENT
        await self.db.update_audit_decision(
            app_id=app_id,
            status="SENT",
            pitch_subject=subject,
            pitch_body=body,
        )
        return True

    async def dispatch_batch(self, limit: int = 50) -> None:
        """Pulls audited leads and delivers emails sequentially with safety controls."""
        audited_leads = await self.db.get_audited_leads(limit=limit)

        if not audited_leads:
            print("[!] No 'AUDITED' leads pending dispatch.")
            return

        print(f"[>] Found {len(audited_leads)} audited leads. Checking delivery conditions...")

        for idx, lead in enumerate(audited_leads):
            # Guard 1: Business hours window
            while not self.is_within_business_hours():
                print(f"[⏸ PAUSED] Outside sending window ({self.start_hour}:00 - {self.end_hour}:00). Sleeping 15 minutes...")
                await asyncio.sleep(900)

            # Guard 2: Daily volume ceiling check
            sent_today = await self.db.get_sent_count_today()
            if sent_today >= self.daily_limit:
                print(f"[🛑 CEILING REACHED] Reached daily threshold of {self.daily_limit} emails. Halting queue.")
                break

            success = await self.send_pitch(lead)

            # Throttle between messages (skip after the final item or if limit reached)
            is_last_item = idx == len(audited_leads) - 1
            if success and not is_last_item and (sent_today + 1) < self.daily_limit:
                await self.apply_throttle_delay()

        print("[>] Dispatch cycle complete!")