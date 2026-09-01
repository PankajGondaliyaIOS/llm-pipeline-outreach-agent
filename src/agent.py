"""
src/agent.py - Multi-Model Fallback Auditor & Pitch Generator.
Cascades across verified Gemini models on 429 quota exhaustion or 503 capacity spikes.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types
from pydantic import ValidationError

from src.config import settings
from src.exceptions import LLMQuotaExhaustedError, LLMServiceUnavailableError
from src.schemas import EconomyAuditResult

logger = logging.getLogger(__name__)

SYSTEM_INSTRUCTION = """
You are an executive business development lead and game economy architect at ZOGOEX LTD.
Your mission is to audit mobile game titles to see if they are a strong fit for our in-game economy infrastructure (virtual game coins, consumable asset trading sinks, booster marketplaces, and web3/fiat hybrid economies).

Evaluation Criteria:
1. DECISION: 'SEND' or 'SKIP'
   - Mark 'SEND' for Midcore, Strategy, Simulation, RPG, Card, Casino, Casual, and Match-3/Puzzle titles that feature high-velocity consumables, boosters, stamina systems, or gacha mechanics.
   - Mark 'SKIP' ONLY if the app is a pure non-game utility or has literally zero virtual items, energy loops, or progression mechanics.
2. AUDIT SCORE: 0.0 to 10.0 based on economy volume and consumable depth.
3. DETECTED MONETIZATION TRAITS: Extract 2-4 specific mechanics.
4. PITCH (Only if 'SEND'):
   - Subject: Direct, compelling, and tailored to the title.
   - Body Formatting Rules:
     * Line 1: "Hi {Game Name} Team,"
     * Paragraph 1 (1 short sentence): Praise their core gameplay, level design, or progression loop.
     * Paragraph 2 (1-2 sentences): Mention that building secondary marketplaces, branded coin settlement, or inventory trade in-house consumes months of dev time away from live ops. Mention that ZOGOEX (https://zogoex.com) provides a turnkey API layer to deploy branded coin and item liquidity rails without backend rework.
     * Value Proposition (EXACTLY 4 concise bullet points starting with "• "):
       • 100% Fiat Settlement: Issue USD game coins and secondary item trading with standard web2 payment rails.
       • Continuous Royalties: Automatic 50% split on every trade fee deposited straight to your developer balance.
       • Economy Safeguards: Built-in price floors keep in-game item values stable across market cycles.
       • Fast REST Integration: Deploy directly into your server stack via webhooks in roughly 48 hours.
     * Closing CTA (1 sentence): "Do you have 10 minutes open later this week for a brief architecture walkthrough?"
     * CRITICAL: DO NOT write any closing or sign-off. End immediately after the CTA question mark.
"""

MODEL_CASCADE = [
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
]


class OutreachAgent:
    """Manages Gemini API interactions with multi-model fallback and error routing."""

    def __init__(self, api_key: Optional[str] = None, models: Optional[List[str]] = None):
        resolved_key = api_key or settings.GEMINI_API_KEY
        if not resolved_key:
            raise ValueError("GEMINI_API_KEY is missing. Ensure it is defined in .env")
        
        self.client = genai.Client(api_key=resolved_key)
        self.models = models or MODEL_CASCADE
        self.max_transient_retries = 2

    async def check_api_health(self) -> bool:
        """Lightweight 1-token probe request to verify quota readiness on primary model."""
        try:
            response = await self.client.aio.models.generate_content(
                model=self.models[0],
                contents="ping",
                config=types.GenerateContentConfig(
                    max_output_tokens=1,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            return bool(response.text)
        except Exception:
            return False

    async def audit_game(self, game_data: Dict[str, Any]) -> EconomyAuditResult:
        """Audits a game title, cascading down models if rate limits or capacity spikes occur."""
        app_name = game_data.get("app_name", "Unknown App")
        prompt = (
            f"Please audit the following mobile game:\n"
            f"- App Name: {app_name}\n"
            f"- Category: {game_data.get('category', 'Games')}\n"
            f"- Minimum Installs: {game_data.get('min_installs', 0):,}\n"
            f"- Developer ID: {game_data.get('developer_id', 'Unknown')}\n"
            f"- Developer Website: {game_data.get('developer_website', 'N/A')}\n"
            f"- PlayStore URL: {game_data.get('playstore_url', 'N/A')}\n"
        )

        last_exception = None

        for model_idx, model_name in enumerate(self.models):
            for attempt in range(1, self.max_transient_retries + 1):
                try:
                    response = await self.client.aio.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_INSTRUCTION,
                            response_mime_type="application/json",
                            response_schema=EconomyAuditResult,
                            temperature=0.2,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                        ),
                    )

                    if not response.text:
                        raise ValueError(f"Empty response returned by model {model_name} for {app_name}")

                    return EconomyAuditResult.model_validate_json(response.text)

                except ValidationError as ve:
                    logger.error("Schema validation failed on model %s for '%s': %s", model_name, app_name, ve)
                    raise ve

                except Exception as e:
                    err_str = str(e)
                    last_exception = e

                    # 429 Quota exhausted -> cascade immediately to next model
                    if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                        logger.warning("[429 QUOTA] Exhausted on %s. Cascading to next model tier...", model_name)
                        break

                    # 503 Capacity spike -> retry once with backoff, then cascade
                    if "503" in err_str or "UNAVAILABLE" in err_str:
                        if attempt < self.max_transient_retries:
                            backoff = 10.0 * attempt
                            logger.warning(
                                "[503 SPIKE] %s busy. Retrying in %.0fs (Attempt %d/%d)...",
                                model_name, backoff, attempt, self.max_transient_retries
                            )
                            await asyncio.sleep(backoff)
                            continue
                        logger.warning("[503 SPIKE] %s unavailable after retries. Cascading...", model_name)
                        break

                    logger.error("Error on %s for '%s': %s", model_name, app_name, e)
                    break

        # If all tiers in the cascade fail, raise for the orchestrator hibernation loop
        raise LLMQuotaExhaustedError(
            f"All model tiers ({', '.join(self.models)}) exhausted while evaluating '{app_name}'."
        ) from last_exception