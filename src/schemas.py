"""
src/schemas.py - Pydantic Data Contracts for LLM Structured Output.
Defines the exact schema Gemini must return when evaluating a game studio.
"""

from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


class OutreachDecision(str, Enum):
    """Restricted decisions the agent can make."""
    SEND = "SEND"
    SKIP = "SKIP"


class EconomyAuditResult(BaseModel):
    """The structured schema enforced on Gemini's JSON output."""
    
    decision: OutreachDecision = Field(
        description="Must be 'SEND' if the game has viable monetization/economy fit, or 'SKIP' if not."
    )
    
    audit_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Fit score between 0.0 and 10.0 indicating how well the game matches our criteria."
    )
    
    reasoning: str = Field(
        description="Concise 1-2 sentence technical reasoning behind the decision."
    )
    
    detected_monetization_traits: List[str] = Field(
        default_factory=list,
        description="List of detected mechanics, e.g. ['gacha', 'secondary marketplace', 'in-game currency', 'ad-driven']."
    )
    
    # These fields are populated only if decision is 'SEND'
# These fields are populated only if decision is 'SEND'
    pitch_subject: Optional[str] = Field(
        default=None,
        description="Targeted, non-spammy email subject line tailored to the game title (required if decision is SEND)."
    )
    
    pitch_body: Optional[str] = Field(
        default=None,
        description=(
            "Strictly formatted email body with line breaks (\\n\\n). "
            "Must contain: 1) 'Hi {Game/Developer Name} Team,', "
            "2) 1-sentence compliment hook, "
            "3) 1-sentence pivot citing https://zogoex.com, "
            "4) exactly 4 concise bullet points starting with '• ', and "
            "5) a 1-sentence 10-minute CTA question. "
            "CRITICAL: Do NOT write any closing or sign-off (no 'Best,', 'Regards,', or name)."
        )
    )