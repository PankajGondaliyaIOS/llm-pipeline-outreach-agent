"""
test_live_delivery.py - Single-target Live Delivery Verification.
Pulls an audited pitch from the database, redirects delivery to your
personal test address, and executes a live SMTP send with company footer.
"""

import asyncio
import sys
from src.config import settings
from src.database import DatabaseManager
from src.dispatcher import EmailDispatcher

# REPLACE THIS WITH YOUR PERSONAL TEST INBOX:
TARGET_TEST_EMAIL = "your_personal_email@gmail.com"


async def run_live_test(recipient: str):
    db = DatabaseManager()
    await db.init_db()

    # 1. Fetch one audited lead
    leads = await db.get_audited_leads(limit=1)
    if not leads:
        print("[!] No 'AUDITED' leads found in the database.")
        print("[*] Run `python3 test_audit_flow.py` first to generate at least one qualified lead.")
        return

    lead = leads[0]
    app_name = lead.get("app_name", "Target App")
    subject = lead.get("pitch_subject", "Quick Introduction: ZOGOEX")
    body = lead.get("pitch_body", "")

    print("=" * 60)
    print("LIVE DELIVERY TEST")
    print(f"Original App: {app_name}")
    print(f"Routing To:   {recipient}")
    print(f"Subject:      {subject}")
    print("=" * 60)

    # 2. Initialize live dispatcher (dry_run=False)
    dispatcher = EmailDispatcher(db_manager=db, dry_run=False)
    
    # 3. Construct message (attaches standard footer)
    msg = dispatcher.build_message(recipient=recipient, subject=subject, body=body)

    print("\n[Message Preview]")
    print(msg.get_content())
    print("-" * 60)

    # 4. Dispatch live via SMTP
    print(f"[*] Connecting to {settings.SMTP_HOST}:{settings.SMTP_PORT}...")
    try:
        await dispatcher.send_via_smtp(msg)
        print(f"\n[✓ SUCCESS] Email sent successfully to {recipient}!")
        print("[*] Check your inbox (and spam/promotions folder) to verify formatting and headers.")
    except Exception as e:
        print(f"\n[✗ FAILED] SMTP dispatch error: {e}")


if __name__ == "__main__":
    test_email = sys.argv[1] if len(sys.argv) > 1 else TARGET_TEST_EMAIL
    if "your_personal_email" in test_email:
        print("[!] Please provide a valid email address:")
        print("    python3 test_live_delivery.py your_email@domain.com")
        sys.exit(1)

    asyncio.run(run_live_test(test_email))