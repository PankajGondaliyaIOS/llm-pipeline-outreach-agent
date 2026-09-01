"""
src/exceptions.py - Enterprise Domain Exception Hierarchy.
"""

class OutreachException(Exception):
    """Base domain exception for the outreach system."""
    pass


class LLMException(OutreachException):
    """Base exception for LLM provider errors."""
    pass


class LLMQuotaExhaustedError(LLMException):
    """Raised when Gemini hits a 429 quota or rate-limit ceiling."""
    def __init__(self, message: str = "LLM quota or rate-limit exhausted.", retry_after_seconds: float = 900.0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMServiceUnavailableError(LLMException):
    """Raised when Gemini returns 503 high-load capacity spikes."""
    pass


class DispatcherDeliveryError(OutreachException):
    """Raised when outbound SMTP delivery fails after all retries."""
    pass