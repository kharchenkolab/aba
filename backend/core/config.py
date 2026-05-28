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
# WORK_DIR is the scratch tier (data.md): per-project, per-run working dirs
# where the sandbox writes intermediates freely. Unregistered, GC'd on a TTL.
# Distinct from ARTIFACTS_DIR (the durable, content-addressed "kept" tier).
WORK_DIR = Path(os.getenv("ABA_WORK_DIR", BASE_DIR / "work")).resolve()
# ENVS_DIR is the materialized-tools area (capabilities.md / capdat_impl.md P1):
# wipeable as a whole (rm -rf → repopulates on demand), kept OUT of the system
# .venv so the backend's env stays pristine. Holds the pylib overlay (one
# shared pip --target dir for Python libs) and conda envs for CLI tools.
ENVS_DIR = Path(os.getenv("ABA_ENVS_DIR", BASE_DIR / "envs")).resolve()
# REFS_DIR is the content-addressed reference store (data.md §4.3): shared,
# deduplicated reference data (genomes, transcriptomes, indices, annotations).
# Distinct from the per-project artifact store; reused across projects.
REFS_DIR = Path(os.getenv("ABA_REFS_DIR", BASE_DIR / "refs")).resolve()
# BIOMNI_DIR is the runtime location of the biomni capability collection
# (collections.md): the dir to put on sys.path so `import biomni.*` works in
# run_python / the kernel. Transitional fallback until biomni is provisioned as
# a real pip install; defaults to the vendored copy at repo root if present.
_biomni_default = BASE_DIR.parent / "biomni"
BIOMNI_DIR = Path(os.getenv("ABA_BIOMNI_DIR", _biomni_default)).resolve() if (
    os.getenv("ABA_BIOMNI_DIR") or _biomni_default.is_dir()) else None

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("ABA_MODEL", "claude-haiku-4-5-20251001")
FAKE_SESSION = os.environ.get("ABA_FAKE_SESSION", "")
# Capability proposal approval (capdat_impl.md P2′): "auto" publishes a
# proposed capability immediately (solo/dev; every add still audited), "ask"
# leaves it proposed for human review (multi-user seam).
CAPABILITY_APPROVAL = os.environ.get("ABA_CAPABILITY_APPROVAL", "auto")
# Persistent kernels (kernels.md): conservative defaults — lazy start, short
# idle TTL, small per-user cap with LRU eviction.
KERNEL_ENABLED = os.environ.get("ABA_KERNEL_ENABLED", "1") not in ("0", "false", "")
KERNEL_IDLE_TTL_S = int(os.environ.get("ABA_KERNEL_IDLE_TTL_S", "900"))   # 15 min
KERNEL_MAX_LIVE = int(os.environ.get("ABA_KERNEL_MAX_LIVE", "5"))         # per user

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
REFS_DIR.mkdir(parents=True, exist_ok=True)
