"""
tests/test_agent.py - Live end-to-end test of Gemini structured generation.
"""

import asyncio
from src.agent import OutreachAgent
from src.schemas import OutreachDecision

async def run_live_test():
    print("[1/2] Initializing OutreachAgent with Gemini 2.5 Flash...")
    agent = OutreachAgent()

    sample_lead = {
        "app_name": "Clash of Empires: Strategy War",
        "category": "Strategy",
        "min_installs": 5000000,
        "developer_id": "Empire Studios Ltd",
        "developer_website": "https://empirestudios.example.com",
        "playstore_url": "https://play.google.com/store/apps/details?id=com.empire.strategy"
    }

    print(f"[2/2] Running audit for: {sample_lead['app_name']}...")
    result = await agent.audit_game(sample_lead)

    print("\n--- Live Gemini Structured Output ---")
    print(f"Decision:    {result.decision.value}")
    print(f"Audit Score: {result.audit_score}/10")
    print(f"Reasoning:   {result.reasoning}")
    print(f"Traits:      {result.detected_monetization_traits}")
    if result.decision == OutreachDecision.SEND:
        print(f"Subject:     {result.pitch_subject}")
        print(f"Pitch Body:\n{result.pitch_body}")
    print("--------------------------------------")
    print("✓ Live Gemini agent test passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_live_test())