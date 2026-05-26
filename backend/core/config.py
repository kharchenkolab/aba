"""Platform configuration: paths, env, model selection.

Domain-neutral. Bio-specific prompt text lives in content/bio/.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent  # backend/
load_dotenv(BASE_DIR.parent / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data")).resolve()
ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", BASE_DIR / "artifacts")).resolve()
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ABA_MODEL", "claude-haiku-4-5-20251001")
FAKE_SESSION = os.environ.get("ABA_FAKE_SESSION", "")

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
