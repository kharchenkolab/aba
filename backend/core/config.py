"""Platform configuration: paths, env, model selection.

Domain-neutral. Bio-specific prompt text lives in content/bio/.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent  # backend/ — SOURCE root, never written at runtime
load_dotenv(BASE_DIR.parent / ".env")

_ABA_RPC_TOKEN = None


def rpc_token() -> str:
    """Process-global secret for the loopback aba_rpc endpoint (the in-kernel `aba`
    backend-reads authenticate with it; the endpoint binds 127.0.0.1). Generated once,
    injected into the interactive kernel env by jupyter._kernel_env and checked by the
    /api/aba_rpc handler — both call this in the SAME backend process, so they agree."""
    global _ABA_RPC_TOKEN
    if _ABA_RPC_TOKEN is None:
        import secrets
        _ABA_RPC_TOKEN = os.environ.get("ABA_RPC_TOKEN") or secrets.token_hex(16)
    return _ABA_RPC_TOKEN

# ABA_RUNTIME_DIR is the roof for all mutable runtime state (data, work, artifacts,
# envs, projects, the workspace DB). Hard-separated from the source tree so:
#   - `git status` is clean (no `?? backend/envs/` etc.)
#   - uvicorn `--reload` doesn't kill kernels when an `ensure_capability` install
#     writes into envs/ (the bug behind 2026-05-31 mid-session kernel deaths)
#   - backups / image snapshots include source-or-runtime by intent, not 33GB of mix
#   - future multi-tenant work just adds an `{ABA_RUNTIME_DIR}/tenants/<tid>/` layer
# Individual sub-paths (DATA_DIR, WORK_DIR, …) each have their own env-var override,
# so a test/eval harness can repoint a single tier without moving everything.
class _LazyDir(os.PathLike):
    """A Path-like whose value is re-resolved from the environment on EVERY use.

    Lets `from core.config import RUNTIME_DIR` stay live: the bound name is this
    proxy, and each operation (`/`, str/os.fspath, .mkdir, .exists, …) resolves
    the CURRENT env value. So a test harness (or a runtime swap) that sets
    ABA_RUNTIME_DIR *after* this module was imported is honored, instead of the
    value being frozen at import time. `/` returns a plain Path (the common case);
    module-level derived constants that must also stay lazy wrap their own
    `_LazyDir(lambda: BASE / "sub")`.
    """
    __slots__ = ("_resolver",)

    def __init__(self, resolver):
        self._resolver = resolver

    def _p(self):
        return self._resolver()

    def __fspath__(self):
        return str(self._resolver())

    def __str__(self):
        return str(self._resolver())

    def __repr__(self):
        return f"LazyDir({self._resolver()!r})"

    def __truediv__(self, other):
        return self._resolver() / other

    def __rtruediv__(self, other):
        return other / self._resolver()

    def __eq__(self, other):
        try:
            return os.fspath(self) == os.fspath(other)
        except TypeError:
            return NotImplemented

    def __hash__(self):
        return hash(str(self._resolver()))

    def __reduce__(self):
        # Pickle as the resolved Path — serialization wants the current value.
        return (Path, (str(self._resolver()),))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._resolver(), name)


def _resolve_runtime_dir() -> Path:
    return Path(os.getenv("ABA_RUNTIME_DIR", "/workspace/aba-runtime")).resolve()


def _resolve_under_runtime(env_key: str, *parts: str) -> Path:
    """Honor a per-tier env override, else derive under the (live) runtime dir."""
    v = os.getenv(env_key)
    base = Path(v) if v else _resolve_runtime_dir().joinpath(*parts)
    return base.resolve()


RUNTIME_DIR = _LazyDir(_resolve_runtime_dir)

# Legacy workspace-level dirs (pre-2026-05-31-reorg). Post-reorg these point at
# the per-project equivalents of `_workspace` (the no-project-active fallback),
# so files don't strand at the runtime root. New code goes through
# project_{data,artifacts,work}_dir(pid) instead; these are kept as the fallback
# for callers without a project context (background jobs, materialize helpers).
DATA_DIR = _LazyDir(lambda: _resolve_under_runtime("DATA_DIR", "projects", "_workspace", "data"))
ARTIFACTS_DIR = _LazyDir(lambda: _resolve_under_runtime("ARTIFACTS_DIR", "projects", "_workspace", "artifacts"))
WORK_DIR = _LazyDir(lambda: _resolve_under_runtime("ABA_WORK_DIR", "projects", "_workspace", "work"))
# ENVS_DIR is the materialized-tools area (capabilities.md / capdat_impl.md P1):
# wipeable as a whole (rm -rf → repopulates on demand), kept OUT of the system
# .venv so the backend's env stays pristine. Holds the pylib overlay (one
# shared pip --target dir for Python libs) and conda envs for CLI tools.
ENVS_DIR = _LazyDir(lambda: _resolve_under_runtime("ABA_ENVS_DIR", "envs"))
# Kernelspecs hardcode the interpreter's absolute path, so they must live and die
# with the env they point at. Scope ABA's Jupyter data dir under ENVS_DIR (not the
# user's global ~/.local/share/jupyter): prod stays self-consistent, and tests —
# which point ABA_ENVS_DIR at a throwaway /tmp env — can no longer poison the
# global dir with specs that dangle once that env is wiped (the bug that DOA'd
# live run_r). Set before any jupyter_client import resolves kernelspec paths.
os.environ["JUPYTER_DATA_DIR"] = str(ENVS_DIR / "jupyter")
# REFS_DIR is the content-addressed reference store (data.md §4.3): shared,
# deduplicated reference data (genomes, transcriptomes, indices, annotations).
# Distinct from the per-project artifact store; reused across projects.
REFS_DIR = _LazyDir(lambda: _resolve_under_runtime("ABA_REFS_DIR", "refs"))

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# Module-load snapshot, kept as the "baseline" — process-startup default + the
# fallback for callers that don't pass one to current_model_for_primary().
MODEL = os.environ.get("ABA_MODEL", "claude-haiku-4-5-20251001")


def current_model_for_primary(default: str = "") -> str:
    """Resolve the model the **primary chat agent** should use *right now*.

    Precedence (live, re-evaluated on every call — no caching):

        1. ABA_PRIMARY_MODEL  in-process env  (targeted override, matches
                                               load_agent_spec's order)
        2. ABA_MODEL          in-process env  (back-compat alias)
        3. ABA_MODEL=...      in ~/.aba/config.env  (rewritten by the
                                               helper's POST /api/auth/model
                                               on tray / Control-page swaps)
        4. EffectiveBundle.settings["default_model"]
                              (deployment-time policy from the layered bundle:
                               system → institution → lab → user. The bundle is
                               cached at process startup, so this is O(1).)
        5. default            caller-supplied  (usually the spec's YAML model)
        6. MODEL              module-load snapshot  (last-resort fallback)

    Hot model switch (misc/mac-install.md § 3c — tray Model submenu): the
    backend reads this at the start of every turn in guide.py, so a switch
    from the helper UI takes effect on the next turn without a restart.

    Robustness: a missing or malformed config.env never raises; we fall
    through to the next layer so an interrupted write or a comment-only
    file doesn't take a turn down. Same for a bundle that fails to load."""
    # Live env vars first — picks up monkeypatch in tests and the launcher-
    # sourced env in production.
    env = (os.environ.get("ABA_PRIMARY_MODEL")
           or os.environ.get("ABA_MODEL"))
    if env and env.strip():
        return env.strip()
    # Fresh re-parse of config.env each call. The file is tiny (handful of
    # export lines) so this is microseconds and avoids the per-turn caching
    # complexity that would otherwise hide cross-turn changes.
    cfg_val = _read_aba_model_from_config_env()
    if cfg_val:
        return cfg_val
    bundle_val = _read_setting_from_bundle("default_model")
    if bundle_val:
        return bundle_val
    return default or MODEL


def current_model_for_project(pid: str | None, default: str = "") -> str:
    """Resolve the primary chat model, PER PROJECT (the user-facing knob).

    Precedence (live, re-evaluated every turn):
        1. ABA_PRIMARY_MODEL  in-process env  (the TARGETED operator override —
                                              a deliberate one-off, outranks all)
        2. the project's selected model       (Settings → LLM; on the registry)
        3. the global chain via current_model_for_primary():
           ABA_MODEL env → ~/.aba/config.env → bundle default_model → caller
           default → snapshot

    Note ABA_MODEL is intentionally BELOW the project: it's the deployment
    DEFAULT (set in .env / config.env), which a per-project selection is meant to
    override. Only the explicit ABA_PRIMARY_MODEL beats a per-project choice.
    `pid=None` collapses to the global resolution (back-compat)."""
    targeted = (os.environ.get("ABA_PRIMARY_MODEL") or "").strip()
    if targeted:
        return targeted
    if pid:
        try:
            from core.projects import project_model
            pm = (project_model(pid) or "").strip()
            if pm:
                return pm
        except Exception:  # noqa: BLE001 — never let a registry read break a turn
            pass
    return current_model_for_primary(default=default)


def _read_setting_from_bundle(key: str) -> str:
    """Return a string-valued setting from the active EffectiveBundle, or
    "" on any failure (bundle not loadable, key absent, non-string value).
    Lazy import keeps core.config importable from inside the bundle stack."""
    try:
        from core.bundle.active import get_bundle
        v = get_bundle().settings.get(key)
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(v, str) and v.strip():
        return v.strip()
    return ""


# ── User-scope preferences (RUNTIME_DIR is per-user, so this store is too) ────
# A small JSON of runtime-toggleable user preferences (e.g. discovery.env_gate),
# distinct from bundle-authored settings.yaml (read-only) and per-project state.
def _user_settings_path() -> Path:
    return Path(str(RUNTIME_DIR)) / "user_settings.json"


def get_user_pref(key: str, default=None):
    import json
    p = _user_settings_path()
    try:
        if p.exists():
            return json.loads(p.read_text()).get(key, default)
    except Exception:  # noqa: BLE001 — a bad prefs file must never break a turn
        pass
    return default


def set_user_pref(key: str, value) -> None:
    """Persist (or clear, when value is None/empty) a user preference."""
    import json
    p = _user_settings_path()
    try:
        d = json.loads(p.read_text()) if p.exists() else {}
    except Exception:  # noqa: BLE001
        d = {}
    if value is None or value == "":
        d.pop(key, None)
    else:
        d[key] = value
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2))


def _read_aba_model_from_config_env() -> str:
    """Parse ~/.aba/config.env for ABA_MODEL. Returns '' on any failure.
    Standalone (no aba_installer dep) so the backend can read it without
    pulling the helper package."""
    home = Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))
    cfg = home / "config.env"
    if not cfg.exists():
        return ""
    try:
        text = cfg.read_text(errors="replace")
    except Exception:  # noqa: BLE001
        return ""
    import re as _re
    # Match the helper's emit_config_env shape:  export ABA_MODEL=<value>
    # Tolerant of optional quotes around the value (shlex.quote leaves
    # single-token strings unquoted but quotes anything weird).
    m = _re.search(r"^\s*(?:export\s+)?ABA_MODEL\s*=\s*"
                   r"(?:'([^']*)'|\"([^\"]*)\"|(\S+))\s*$",
                   text, _re.MULTILINE)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or m.group(3) or "").strip()
FAKE_SESSION = os.environ.get("ABA_FAKE_SESSION", "")
# Capability proposal approval (capdat_impl.md P2′): "auto" publishes a
# proposed capability immediately (solo/dev; every add still audited), "ask"
# leaves it proposed for human review (multi-user seam).
CAPABILITY_APPROVAL = os.environ.get("ABA_CAPABILITY_APPROVAL", "auto")
# Persistent kernels (kernels.md): conservative defaults — lazy start, short
# idle TTL, small per-user cap with LRU eviction.
KERNEL_ENABLED = os.environ.get("ABA_KERNEL_ENABLED", "1") not in ("0", "false", "")
KERNEL_IDLE_TTL_S = int(os.environ.get("ABA_KERNEL_IDLE_TTL_S", "3600"))  # 1 h (was 15 min — too eager; state-bearing kernels are expensive to rebuild)
KERNEL_MAX_LIVE = int(os.environ.get("ABA_KERNEL_MAX_LIVE", "5"))         # per user — SOFT cap (evict idle LRU)
# Absolute ceiling. The soft cap only evicts IDLE kernels; when all live kernels
# are busy we allow a bounded burst above the soft cap rather than kill running
# work, refusing only past this hard cap. Keep it modest on shared systems.
KERNEL_HARD_MAX = int(os.environ.get("ABA_KERNEL_HARD_MAX", str(KERNEL_MAX_LIVE + 3)))

# ── History compaction + tool-output cap ────────────────────────────────────
# Two layers (misc/history_compaction_redesign.md):
#   Layer A — deterministic K-window pruning (every turn). Cheap. The K is a
#     trade-off vs. prompt cache: every aging-out event mutates a block sitting
#     INSIDE the cached message-tail prefix, invalidating cache from that point
#     forward. Setting K too low ⇒ continuous cache churn; too high ⇒ wasted
#     tokens on stale verbose tool_results. K=30 matches CC's "auto-compact
#     rarely fires" behavior in practice — within a recipe execution the K-window
#     almost never trims, so the message-tail prefix extends cleanly turn over turn.
#   Layer B — LLM-based thread summary, fires only when the pruned messages
#     STILL exceed THRESHOLD chars. Catastrophic for cache (replaces the whole
#     message tail with a synthesized block) so the threshold sits high. 400K
#     chars ≈ 100K tokens at ~4 chars/token — matches CC's autoCompactWindow
#     default. Override via env to raise on Sonnet (200K ctx) or higher.
HISTORY_K_TOOL_KEEP = int(os.environ.get("ABA_HISTORY_K_TOOL_KEEP", "30"))
HISTORY_K_TEXT_KEEP = int(os.environ.get("ABA_HISTORY_K_TEXT_KEEP", "12"))
HISTORY_SUMMARY_THRESHOLD_CHARS = int(
    os.environ.get("ABA_HISTORY_SUMMARY_THRESHOLD_CHARS", "400000")
)

# Live-tail of run_r/run_python output: chunks emitted from the kernel are
# coalesced before being forwarded as `tool_chunk` SSE events, so a chatty
# cell (R progress bars, install logs, tqdm) doesn't flood the wire AND the
# UI gets human-readable bursts instead of a stream-per-millisecond jitter.
# Flush fires when EITHER cap is hit, whichever first. Tunable via env.
TOOL_STREAM_FLUSH_BYTES = int(os.environ.get("ABA_TOOL_STREAM_FLUSH_BYTES", "10240"))
TOOL_STREAM_FLUSH_INTERVAL_S = float(os.environ.get("ABA_TOOL_STREAM_FLUSH_INTERVAL_S", "0.5"))

# Per-tool stdout/stderr cap applied AT INPUT TIME — when a kernel result
# becomes a tool_result block. Capped text is what enters history and what
# the prompt cache extends over; without this, a single huge dataframe print
# can fill the Layer B threshold by itself. Middle-snip (vs. CC's tail-truncate)
# preserves the informative head AND tail — both are signal in scientific
# output (df.head() at the top, summary/return value at the bottom; middle is
# typically repetition or progress lines). Set to 0 to disable capping.
TOOL_OUTPUT_CAP_CHARS = int(os.environ.get("ABA_TOOL_OUTPUT_CAP_CHARS", "50000"))

ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
REFS_DIR.mkdir(parents=True, exist_ok=True)
(ENVS_DIR / "jupyter").mkdir(parents=True, exist_ok=True)


# ── Project-scoped runtime accessors (2026-05-31 reorg) ─────────────────────
# Everything that belongs to a project lives under projects/<pid>/ — a single
# directory you can back up, export, or delete atomically. Workspace-level
# fallback dirs (the legacy DATA_DIR etc.) are kept for the "no project active"
# case (background jobs without context, the workspace registry, scratch DBs).
PROJECTS_DIR = _LazyDir(lambda: _resolve_under_runtime("ABA_PROJECTS_DIR", "projects"))
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


_NO_PROJECT_FALLBACK = "_workspace"
# Legacy callers pre-reorg used the strings "default" / "None" / passed None
# when no project was active. Coerce them all to the same workspace-level dir
# so we don't end up with sibling `projects/default/`, `projects/None/`,
# `projects/_workspace/` all meaning "no project."
_PROJECT_FALLBACKS = {"", "default", "None", "none"}


def project_root(pid: str) -> Path:
    """projects/<pid>/ — the per-project consolidated root. Falsy or legacy
    fallback project IDs ('', 'default', 'None') all map to _workspace/."""
    if not pid or pid in _PROJECT_FALLBACKS:
        pid = _NO_PROJECT_FALLBACK
    p = PROJECTS_DIR / pid
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_db_path(pid: str) -> Path:
    return project_root(pid) / "project.db"


def project_data_dir(pid: str) -> Path:
    p = project_root(pid) / "data"
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_work_dir(pid: str) -> Path:
    p = project_root(pid) / "work"
    p.mkdir(parents=True, exist_ok=True)
    return p


def project_artifacts_dir(pid: str) -> Path:
    p = project_root(pid) / "artifacts"
    p.mkdir(parents=True, exist_ok=True)
    return p


# current_project_id moved to core.projects (burn-down #1 — config is a leaf,
# must not import projects).

