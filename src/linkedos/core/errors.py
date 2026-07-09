"""Exception hierarchy for linkedos.

Every error raised on purpose by this project derives from `LinkedOSError`, so a
caller can distinguish "our fault" from a genuine bug or a third-party failure.
"""

from __future__ import annotations


class LinkedOSError(Exception):
    """Base class for all linkedos errors."""


class ConfigError(LinkedOSError):
    """Settings are missing, malformed, or mutually inconsistent."""


class ProviderError(LinkedOSError):
    """An LLM provider (Anthropic, Ollama) failed or returned an unusable response."""


class IntegrationError(LinkedOSError):
    """An external non-LLM integration (e.g. the LinkedIn API) failed."""


class WorkflowError(LinkedOSError):
    """A service-layer workflow could not complete."""


class RateLimitError(LinkedOSError):
    """An upstream rate limit or budget cap was hit."""
