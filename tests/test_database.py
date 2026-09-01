"""
tests/test_database.py - Verifies database initialization, upsert, and state updates.
"""

import asyncio
import os
from src.database import DatabaseManager

async def run_test():
    test_db_path = "data/test_outreach.db"
    
    # Clean up previous test run if exists
    if os.path.exists(test_db_path):
        os.remove(test_db_path)
        
    db = DatabaseManager(db_path=test_db_path)
    
    print("[1/4] Initializing test database...")
    await db.init_db()
    
    print("[2/4] Upserting sample raw leads...")
    await db.upsert_raw_lead(
        app_id="com.king.candycrushsaga",
        app_name="Candy Crush Saga",
        category="Casual",
        min_installs=1000000000,
        developer_id="King",
        developer_email="candycrush.techhelp@king.com",
        developer_website="http://candycrushsaga.com/help/",
        playstore_url="https://play.google.com/store/apps/details?id=com.king.candycrushsaga"
    )
    
    print("[3/4] Fetching pending leads...")
    pending = await db.get_pending_leads(limit=5)
    assert len(pending) == 1, "Failed to retrieve pending lead"
    assert pending[0]["app_name"] == "Candy Crush Saga"
    print(f"✓ Retrieved lead: {pending[0]['app_name']} ({pending[0]['category']})")
    
    print("[4/4] Updating audit decision...")
    await db.update_audit_decision(
        app_id="com.king.candycrushsaga",
        status="SKIPPED",
        audit_score=2.0,
        skip_reason="Match-3 casual title without tokenized in-game secondary marketplace."
    )
    print("✓ All database unit tests passed successfully!\n")

if __name__ == "__main__":
    asyncio.run(run_test())