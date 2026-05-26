"""Re-export shim — config split into core/config.py (platform) and
content/bio/config.py (bio prompts) during arch3 Pass A.

This shim keeps `from config import …` working through Pass A. It is
dropped in Pass B once all callers migrate to the proper imports.
"""
from core.config import (  # noqa: F401
    BASE_DIR, DATA_DIR, ARTIFACTS_DIR, API_KEY, MODEL, FAKE_SESSION,
)
from content.bio.config import SYSTEM_PROMPT  # noqa: F401
