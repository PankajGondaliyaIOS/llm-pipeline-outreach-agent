"""
test_audit_flow.py - CSV Ingestion & Gemini Audit Validation.
Reads target leads from CSV, audits developer & game metadata via Gemini,
extracts contact email, and qualifies leads until 2 pass with AUDITED status.
"""

import asyncio
import csv
import json
import re
import logging
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from google import genai
from google.genai import types

from src.config import settings
from src.database import DatabaseManager
from src.dispatcher import EmailDispatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_test")


def extract_package_id(playstore_url: str) -> str:
    """Extracts package id parameter from Play Store URL."""
    try:
        parsed = urlparse(playstore_url)
        params = parse_qs(parsed.query)
        if "id" in params:
            return params["id"][0].strip()
    except Exception:
        pass
    return playstore_url.rstrip("/").split("=")[-1]


def read_leads_from_csv(file_path: str, max_records: int = 15) -> list[dict]:
    """Parses candidate records directly from the specified CSV format."""
    records = []
    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row or not row.get("App Name"):
                continue

            playstore_url = (row.get("PlayStore_URL") or "").strip()
            app_id = extract_package_id(playstore_url)

            # Clean float strings like '10.0' into clean integer
            raw_installs = row.get("Minimum Installs", "0").split(".")[0].strip()
            min_installs = int(raw_installs) if raw_installs.isdigit() else 0

            records.append({
                "app_id": app_id,
                "app_name": (row.get("App Name") or "").strip(),
                "category": (row.get("Category") or "Tools").strip(),
                "min_installs": min_installs,
                "developer_id": (row.get("Developer Id") or "").strip(),
                "developer_email": (row.get("Developer Email") or "").strip(),
                "developer_website": (row.get("Developer Website") or "").strip(),
                "playstore_url": playstore_url,
            })
            if len(records) >= max_records:
                break

    return records


async def audit_lead_with_gemini(client: genai.Client, lead: dict) -> dict:
    """
    Evaluates app fit using gemini-3.1-flash-lite with structured JSON output,
    generates concise bulleted pitch copy, and handles 429 rate-limit backoff.
    """
    prompt = f"""
You are an executive partnership director at ZOGOEX LTD writing high-converting, concise B2B cold emails to mobile game developers.

App Information:
- App Name: {lead['app_name']}
- Package ID: {lead['app_id']}
- Category: {lead['category']}
- Developer: {lead['developer_id']}
- Listed Contact Email: {lead['developer_email']}
- Website: {lead['developer_website']}
- Play Store URL: {lead['playstore_url']}

Your task:
1. Determine if this application represents a viable studio/app for strategic tech collaboration or economy integration with ZOGOEX.
2. If approved (PASS), craft an ultra-concise pitch following this EXACT structure and tone:

Structure Rules:
- Greeting: "Hi {{Developer or App}} Team,"
- Opener (1 sentence max): Genuine compliment referencing specific gameplay mechanics, visuals, or design of {lead['app_name']}.
- Pivot & Problem (1-2 sentences): Explain that building custom currency settlement, secondary marketplaces, or virtual economies takes months of dev time away from core gameplay. Mention ZOGOEX - https://zogoex.com offers a production-ready API layer so your team does not need to build from scratch:
- Value Bullets: Exactly 3 to 4 punchy, bolded bullet points structured like:
  * 100% Fiat Settlement: Issue USD game coins and secondary item trading with standard web2 payment rails.
  * Continuous Royalties: Automatic 50% split on every trade fee deposited straight to your developer balance.
  * Economy Safeguards: Built-in price floors keep in-game item values stable across market cycles.
  * Fast REST Integration: Deploy directly into your server stack via webhooks in roughly 48 hours.
- Call to Action (1 sentence): "Do you have 10 minutes open later this week for a brief architecture walkthrough?"
- Sign-off: End with "Best," (the mandatory executive signature is appended automatically by the dispatcher).

Respond strictly in valid JSON format:
{{
  "decision": "PASS" or "REJECT",
  "audit_score": 8.5,
  "reasoning": "Clear justification based on category, scale, and fit",
  "verified_email": "{lead['developer_email']}",
  "pitch_subject": "Punchy 4-7 word subject line referencing {lead['app_name']} and economy infrastructure",
  "pitch_body": "The full formatted email text following the exact structure above"
}}
"""
    max_retries = 3
    backoff_delay = 5

    for attempt in range(max_retries):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-3.1-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.7,
                ),
            )
            raw_text = response.text.strip()
            if "```" in raw_text:
                match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.DOTALL)
                if match:
                    raw_text = match.group(1).strip()
            return json.loads(raw_text)

        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                if attempt < max_retries - 1:
                    print(f"[!] Rate limit hit (429). Retrying in {backoff_delay}s (Attempt {attempt + 1}/{max_retries})...")
                    await asyncio.sleep(backoff_delay)
                    backoff_delay *= 2
                    continue
            raise e

    return {"decision": "REJECT", "reasoning": "Rate limit exceeded after retries"}


async def main():
    csv_path = "leads.csv"
    if not Path(csv_path).exists():
        print(f"[!] File not found: {csv_path}. Please place your CSV file in the root directory.")
        return

    db = DatabaseManager()
    await db.init_db()
    gemini_client = genai.Client(api_key=settings.GEMINI_API_KEY)

    print(f"[*] Reading records from {csv_path}...")
    raw_leads = read_leads_from_csv(csv_path, max_records=15)
    print(f"[✓] Extracted {len(raw_leads)} records. Ingesting into database...")

    for lead in raw_leads:
        if lead["app_id"]:
            await db.upsert_raw_lead(
                app_id=lead["app_id"],
                app_name=lead["app_name"],
                category=lead["category"],
                min_installs=lead["min_installs"],
                developer_id=lead["developer_id"],
                developer_email=lead["developer_email"],
                developer_website=lead["developer_website"],
                playstore_url=lead["playstore_url"],
            )

    pending = await db.get_pending_leads(limit=15)
    passed_count = 0
    target_passed = 2

    print(f"\n[*] Starting Gemini audits (Target: {target_passed} approved)...")

    for lead in pending:
        if passed_count >= target_passed:
            break

        print(f"\n------------------------------------------------------------")
        print(f"[*] Evaluating: {lead['app_name']} ({lead['app_id']})")
        print(f"    Website: {lead['developer_website']}")
        print(f"    Current Email: {lead['developer_email']}")

        try:
            audit = await audit_lead_with_gemini(gemini_client, lead)
        except Exception as e:
            logger.error("Audit call failed for %s: %s", lead["app_id"], e)
            continue

        decision = audit.get("decision", "REJECT").upper()
        score = float(audit.get("audit_score", 5.0))
        reasoning = audit.get("reasoning", "")
        verified_email = audit.get("verified_email") or lead["developer_email"]

        print(f"\n-> Gemini Decision: {decision} (Score: {score}/10)")
        print(f"-> Verified Email:  {verified_email}")
        print(f"-> Rationale:       {reasoning}")

        if decision == "PASS":
            passed_count += 1
            await db.upsert_raw_lead(
                app_id=lead["app_id"],
                app_name=lead["app_name"],
                category=lead["category"],
                min_installs=lead["min_installs"],
                developer_id=lead["developer_id"],
                developer_email=verified_email,
                developer_website=lead["developer_website"],
                playstore_url=lead["playstore_url"],
            )
            await db.update_audit_decision(
                app_id=lead["app_id"],
                status="AUDITED",
                audit_score=score,
                pitch_subject=audit.get("pitch_subject"),
                pitch_body=audit.get("pitch_body"),
            )
            print(f"[✓ QUALIFIED {passed_count}/{target_passed}] Updated status to 'AUDITED'.")
        else:
            await db.update_audit_decision(
                app_id=lead["app_id"],
                status="SKIPPED",
                audit_score=score,
                skip_reason=reasoning,
            )
            print("[✗ REJECTED] Marked status as 'SKIPPED'.")

    print(f"\n============================================================")
    print(f"[*] Audit run complete. Total qualified leads: {passed_count}")
    print(f"[*] Initiating dry-run delivery to preview messages and signatures...")
    print(f"============================================================")

    dispatcher = EmailDispatcher(db_manager=db, dry_run=True)
    dispatcher.start_hour = 0
    dispatcher.end_hour = 24
    dispatcher.delay_min = 1
    dispatcher.delay_max = 2

    await dispatcher.dispatch_batch(limit=target_passed)


if __name__ == "__main__":
    asyncio.run(main())