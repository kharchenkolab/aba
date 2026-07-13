"""Platform configuration: paths, env, model selection.

Domain-neutral. Bio-specific prompt text lives in content/bio/.

Settings registry (env_reorg)
=============================
Every ``ABA_*`` setting the backend consumes is DECLARED here via ``setting()``
and read through the resulting accessor — this module is the single, enforced
read path. ``tests/test_env_registry_guard.py`` fails the build if any inline
``os.environ``/``os.getenv`` read of an ``ABA_*`` name survives in ``backend/``
outside this file, so ``list_settings()`` can never silently under-report.

Each declaration also carries migration metadata:
  * ``weft_fate`` ∈ {keep, retire, move:site, move:envspec, revisit} — what the
    later weft rewrite does with it (this ledger is weft's move/delete checklist).
  * ``reduction``  ∈ {keep, dead, resolve-flag, merge:<g>, derive:<from>,
    relocate:<layer>} — the fewer-better-variables plan (§6 of env_reorg.md).

Stdlib-only, import-safe: this module loads very early, so the registry must not
import bundle/graph/runtime. Resolution is LAZY by default — every ``.get()``
re-reads the environment — matching the historical ``_LazyDir`` contract the
test harness relies on (it sets ``ABA_RUNTIME_DIR``/``ABA_DB_PATH`` after import).
Path settings additionally bind their public name to a ``_LazyDir`` (unchanged
runtime behavior); scalar settings bind the frozen ``.get()`` value so the ~60
modules importing these names see no change.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent  # backend/ — SOURCE root, never written at runtime
load_dotenv(BASE_DIR.parent / ".env")

# ═══════════════════════════════════════════════════════════════════════════
# Settings registry primitives
# ═══════════════════════════════════════════════════════════════════════════
_REGISTRY: "dict[str, Setting]" = {}
_MISSING = object()

_TRUE_TOKENS = ("1", "true", "yes", "on")


def _coerce_bool_default_on(raw):
    """Historical 'on unless explicitly disabled' idiom (``not in {0,false,''}``)."""
    return str(raw) not in ("0", "false", "")


def _coerce_bool(raw):
    """Standard 'off unless explicitly enabled' idiom."""
    return str(raw).strip().lower() in _TRUE_TOKENS


def _coerce_int(raw):
    return int(raw)


def _coerce_float(raw):
    return float(raw)


def _coerce_str(raw):
    return str(raw)


def _coerce_path(raw):
    return Path(raw).resolve()


def _coerce_csv(raw):
    """Comma-separated → tuple of trimmed non-empty tokens."""
    return tuple(t.strip() for t in str(raw).split(",") if t.strip())


_COERCERS = {"bool": _coerce_bool, "int": _coerce_int, "float": _coerce_float,
             "str": _coerce_str, "path": _coerce_path, "csv": _coerce_csv}


class Setting:
    """One declared configuration setting: metadata + a resolution path.

    Read the live value with ``.get()`` (re-resolves each call unless
    ``resolve='once'``). ``resolve()`` returns ``(value, source)`` for
    introspection (``list_settings`` / ``aba doctor``). A ``resolver`` callable,
    when given, fully owns resolution (used for composite settings like the model
    or credential precedence chains); otherwise the first present env key wins,
    else the declared default.
    """
    __slots__ = ("name", "env_keys", "type", "default", "_coerce", "resolver",
                 "category", "doc", "branches", "deploy_injected", "secret",
                 "weft_fate", "reduction", "enum", "resolve_mode",
                 "_cache", "_has_cache")

    def __init__(self, *, name, env, type, default, coerce, resolver, category,
                 doc, branches, deploy_injected, secret, weft_fate, reduction,
                 enum, resolve_mode):
        self.name = name
        self.env_keys = [env] if isinstance(env, str) else list(env or [])
        self.type = type
        self.default = default
        self._coerce = coerce or _COERCERS.get(type, _coerce_str)
        self.resolver = resolver
        self.category = category
        self.doc = doc
        self.branches = branches
        self.deploy_injected = deploy_injected
        self.secret = secret
        self.weft_fate = weft_fate
        self.reduction = reduction
        self.enum = enum
        self.resolve_mode = resolve_mode
        self._cache = _MISSING
        self._has_cache = False

    def resolve(self):
        """Return ``(value, source)``. source ∈ {``env:KEY``, ``resolver``, ``default``}."""
        if self.resolve_mode == "once" and self._has_cache:
            return self._cache
        pair = self._resolve_fresh()
        if self.resolve_mode == "once":
            self._cache = pair
            self._has_cache = True
        return pair

    def _resolve_fresh(self):
        if self.resolver is not None:
            v = self.resolver()
            if isinstance(v, tuple) and len(v) == 2:
                return v
            return v, "resolver"
        for k in self.env_keys:
            if k not in os.environ:
                continue
            raw = os.environ[k]
            # An explicitly-empty numeric/path env value is treated as unset
            # (historically `int(os.environ.get(k, "5"))` would crash on "");
            # str/csv accept "" as a real value (matching os.environ.get(k, "")).
            if raw == "" and self.type not in ("str", "csv"):
                continue
            try:
                v = self._coerce(raw)
            except Exception:  # noqa: BLE001 — malformed value → default, never crash
                return self.default, f"env:{k}(coerce-failed)"
            # enum is ADVISORY in the mechanical pass: an out-of-enum value still
            # passes through (matching the historical inline read), but the source
            # is flagged so `aba doctor` surfaces the drift.
            if self.enum and v not in self.enum:
                return v, f"env:{k}(not-in-enum)"
            return v, f"env:{k}"
        return self.default, "default"

    def get(self):
        return self.resolve()[0]

    def __repr__(self):
        return f"Setting({self.name!r}, env={self.env_keys})"


def setting(name, *, env=None, type="str", default=None, coerce=None,
            resolver=None, category="", doc="", branches=False,
            deploy_injected=False, secret=False, weft_fate="keep",
            reduction="keep", enum=None, resolve="lazy"):
    """Declare a setting once; register it and return the ``Setting`` accessor.

    Callers read the live value via the returned object's ``.get()`` or via
    ``settings.<name>.get()``. Scalar back-compat module constants bind the frozen
    ``.get()`` value; path tiers go through ``_path_setting`` (see below)."""
    if name in _REGISTRY:
        raise ValueError(f"setting {name!r} already declared")
    s = Setting(name=name, env=env, type=type, default=default, coerce=coerce,
                resolver=resolver, category=category, doc=doc, branches=branches,
                deploy_injected=deploy_injected, secret=secret,
                weft_fate=weft_fate, reduction=reduction, enum=enum,
                resolve_mode=resolve)
    _REGISTRY[name] = s
    return s


class _SettingsFacade:
    """Attribute access to registered settings: ``settings.kernel_enabled.get()``."""
    def __getattr__(self, name):
        try:
            return _REGISTRY[name]
        except KeyError:
            raise AttributeError(f"no setting {name!r} registered")

    def __iter__(self):
        return iter(_REGISTRY.values())

    def __contains__(self, name):
        return name in _REGISTRY


settings = _SettingsFacade()


def get_setting(name):
    return _REGISTRY[name]


_SECRET_REDACTION = "••••"


def _redact(val):
    s = str(val or "")
    if not s:
        return ""
    return (s[:2] + _SECRET_REDACTION + s[-2:]) if len(s) > 6 else _SECRET_REDACTION


def list_settings(*, include_unknown=True, reveal_secrets=False):
    """Full declared surface with resolved values + sources, for ``aba doctor``.

    Secrets are redacted unless ``reveal_secrets``. When ``include_unknown``, also
    lists ``ABA_*`` env vars present in the process that are NOT declared — the
    drift / typo detector that a bypass-proof registry makes trustworthy."""
    rows = []
    for s in _REGISTRY.values():
        try:
            val, src = s.resolve()
        except Exception as e:  # noqa: BLE001
            val, src = f"<error: {e}>", "error"
        shown = _redact(val) if (s.secret and not reveal_secrets) else val
        rows.append({
            "name": s.name, "env": s.env_keys, "value": shown, "source": src,
            "type": s.type, "category": s.category, "branches": s.branches,
            "deploy_injected": s.deploy_injected, "secret": s.secret,
            "weft_fate": s.weft_fate, "reduction": s.reduction, "doc": s.doc,
        })
    rows.sort(key=lambda r: (r["category"], r["name"]))
    result = {"settings": rows}
    if include_unknown:
        declared = {k for s in _REGISTRY.values() for k in s.env_keys}
        result["unknown_env"] = sorted(
            k for k in os.environ
            if k.startswith("ABA_") and k not in declared)
    return result

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


def _path_setting(name, env, resolver, *, doc="", deploy_injected=False,
                  weft_fate="keep", reduction="keep"):
    """Register a path tier in the settings registry and return a ``_LazyDir``
    bound to the SAME resolver, so the public name stays byte-for-byte the lazy
    proxy it always was (the test harness repoints these via env after import).
    The registered ``Setting`` shares the resolver purely for introspection."""
    setting(name, env=env, type="path", default=None, resolver=resolver,
            category="paths", doc=doc, deploy_injected=deploy_injected,
            weft_fate=weft_fate, reduction=reduction)
    return _LazyDir(resolver)


RUNTIME_DIR = _path_setting(
    "runtime_dir", "ABA_RUNTIME_DIR", _resolve_runtime_dir,
    doc="Root for all mutable runtime state (projects, envs, refs, workspace DB).",
    deploy_injected=True)

# Legacy workspace-level dirs (pre-2026-05-31-reorg). Post-reorg these point at
# the per-project equivalents of `_workspace` (the no-project-active fallback),
# so files don't strand at the runtime root. New code goes through
# project_{data,artifacts,work}_dir(pid) instead; these are kept as the fallback
# for callers without a project context (background jobs, materialize helpers).
DATA_DIR = _path_setting(
    "data_dir", "DATA_DIR",
    lambda: _resolve_under_runtime("DATA_DIR", "projects", "_workspace", "data"),
    doc="Workspace-level data dir (no-project fallback).")
ARTIFACTS_DIR = _path_setting(
    "artifacts_dir", "ARTIFACTS_DIR",
    lambda: _resolve_under_runtime("ARTIFACTS_DIR", "projects", "_workspace", "artifacts"),
    doc="Workspace-level artifacts dir (no-project fallback).")
WORK_DIR = _path_setting(
    "work_dir", "ABA_WORK_DIR",
    lambda: _resolve_under_runtime("ABA_WORK_DIR", "projects", "_workspace", "work"),
    doc="Workspace-level work dir (no-project fallback).")
# ENVS_DIR is the materialized-tools area (capabilities.md / capdat_impl.md P1):
# wipeable as a whole (rm -rf → repopulates on demand), kept OUT of the system
# .venv so the backend's env stays pristine. Holds the pylib overlay (one
# shared pip --target dir for Python libs) and conda envs for CLI tools.
ENVS_DIR = _path_setting(
    "envs_dir", "ABA_ENVS_DIR",
    lambda: _resolve_under_runtime("ABA_ENVS_DIR", "envs"),
    doc="Materialized-tools area (conda envs + pylib overlay); wipeable whole.",
    deploy_injected=True, weft_fate="move:envspec")
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
REFS_DIR = _path_setting(
    "refs_dir", "ABA_REFS_DIR",
    lambda: _resolve_under_runtime("ABA_REFS_DIR", "refs"),
    doc="Content-addressed shared reference store (genomes, indices, annotations).")

API_KEY = setting(
    "anthropic_api_key", env="ANTHROPIC_API_KEY", type="str", default="",
    category="model", secret=True, weft_fate="keep",
    doc="Anthropic API key — module-load snapshot (live reads go via core.llm).",
).get()
# Module-load snapshot, kept as the "baseline" — process-startup default + the
# fallback for callers that don't pass one to current_model_for_primary().
MODEL = setting(
    "model_snapshot", env="ABA_MODEL", type="str",
    default="claude-haiku-4-5-20251001", category="model", branches=True,
    weft_fate="keep", reduction="merge:model",
    doc="Process-startup model snapshot; last-resort fallback in the model resolver.",
).get()


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


def set_default_model(model: str) -> None:
    """Persist the INSTALL-WIDE default model (ABA_MODEL) to $ABA_HOME/config.env and
    apply it live in-process. Empty string clears the override (revert to the bundle
    default). This is the no-project counterpart to a per-project pin — it's what the
    Settings → Model picker writes when no project is open. Other config.env lines
    (ABA_ENV_PREWARM, ABA_REF, …) are preserved."""
    import re as _re
    import shlex
    model = (model or "").strip()
    # Live in-process first, so the change takes effect on the next turn without a restart.
    if model:
        os.environ["ABA_MODEL"] = model
    else:
        os.environ.pop("ABA_MODEL", None)
    home = Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))
    cfg = home / "config.env"
    try:
        lines = cfg.read_text(errors="replace").splitlines() if cfg.exists() else []
    except Exception:  # noqa: BLE001
        lines = []
    pat = _re.compile(r"^\s*(?:export\s+)?ABA_MODEL\s*=")
    lines = [ln for ln in lines if not pat.match(ln)]
    if model:
        lines.append(f"ABA_MODEL={shlex.quote(model)}")
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("\n".join(lines) + ("\n" if lines else ""))


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
FAKE_SESSION = setting(
    "fake_session", env="ABA_FAKE_SESSION", type="str", default="",
    category="mode", branches=True, weft_fate="keep",
    doc="Non-empty → deterministic fake LLM session (tests / demos).",
).get()
# Capability proposal approval (capdat_impl.md P2′): "auto" publishes a
# proposed capability immediately (solo/dev; every add still audited), "ask"
# leaves it proposed for human review (multi-user seam).
CAPABILITY_APPROVAL = setting(
    "capability_approval", env="ABA_CAPABILITY_APPROVAL", type="str",
    default="auto", enum=("auto", "ask"), category="behavior", branches=True,
    weft_fate="keep",
    doc="'auto' publishes proposed capabilities immediately; 'ask' holds for review.",
).get()
# Persistent kernels (kernels.md): conservative defaults — lazy start, short
# idle TTL, small per-user cap with LRU eviction.
KERNEL_ENABLED = setting(
    "kernel_enabled", env="ABA_KERNEL_ENABLED", type="bool", default=True,
    coerce=_coerce_bool_default_on, category="behavior", branches=True,
    weft_fate="revisit",
    doc="Master switch for the interactive kernel lane; off → stateless one-shot exec.",
).get()
KERNEL_IDLE_TTL_S = setting(
    "kernel_idle_ttl_s", env="ABA_KERNEL_IDLE_TTL_S", type="int", default=3600,
    category="tuning", weft_fate="revisit", reduction="merge:kernel",
    doc="Idle kernel time-to-live in seconds before LRU eviction.",
).get()
KERNEL_MAX_LIVE = setting(
    "kernel_max_live", env="ABA_KERNEL_MAX_LIVE", type="int", default=5,
    category="tuning", weft_fate="revisit", reduction="merge:kernel",
    doc="Per-user SOFT cap on live kernels (evict idle LRU past this).",
).get()
# Absolute ceiling. The soft cap only evicts IDLE kernels; when all live kernels
# are busy we allow a bounded burst above the soft cap rather than kill running
# work, refusing only past this hard cap. Keep it modest on shared systems.
KERNEL_HARD_MAX = setting(
    "kernel_hard_max", env="ABA_KERNEL_HARD_MAX", type="int",
    default=KERNEL_MAX_LIVE + 3, category="tuning", weft_fate="revisit",
    reduction="derive:kernel_max_live",
    doc="Absolute ceiling on live kernels (default = kernel_max_live + 3).",
).get()

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
HISTORY_K_TOOL_KEEP = setting(
    "history_k_tool_keep", env="ABA_HISTORY_K_TOOL_KEEP", type="int", default=30,
    category="tuning", weft_fate="keep", reduction="merge:history",
    doc="Layer-A window: number of recent tool_result blocks kept verbatim.",
).get()
HISTORY_K_TEXT_KEEP = setting(
    "history_k_text_keep", env="ABA_HISTORY_K_TEXT_KEEP", type="int", default=12,
    category="tuning", weft_fate="keep", reduction="merge:history",
    doc="Layer-A window: number of recent text turns kept verbatim.",
).get()
HISTORY_SUMMARY_THRESHOLD_CHARS = setting(
    "history_summary_threshold_chars", env="ABA_HISTORY_SUMMARY_THRESHOLD_CHARS",
    type="int", default=400000, category="tuning", weft_fate="keep",
    reduction="merge:history",
    doc="Layer-B trigger: summarize when pruned history still exceeds this many chars.",
).get()

# Live-tail of run_r/run_python output: chunks emitted from the kernel are
# coalesced before being forwarded as `tool_chunk` SSE events, so a chatty
# cell (R progress bars, install logs, tqdm) doesn't flood the wire AND the
# UI gets human-readable bursts instead of a stream-per-millisecond jitter.
# Flush fires when EITHER cap is hit, whichever first. Tunable via env.
TOOL_STREAM_FLUSH_BYTES = setting(
    "tool_stream_flush_bytes", env="ABA_TOOL_STREAM_FLUSH_BYTES", type="int",
    default=10240, category="tuning", weft_fate="keep", reduction="merge:tool_stream",
    doc="Coalesce kernel output into a tool_chunk SSE event once this many bytes buffer.",
).get()
TOOL_STREAM_FLUSH_INTERVAL_S = setting(
    "tool_stream_flush_interval_s", env="ABA_TOOL_STREAM_FLUSH_INTERVAL_S",
    type="float", default=0.5, category="tuning", weft_fate="keep",
    reduction="merge:tool_stream",
    doc="Max seconds to hold buffered kernel output before flushing a tool_chunk.",
).get()

# Per-tool stdout/stderr cap applied AT INPUT TIME — when a kernel result
# becomes a tool_result block. Capped text is what enters history and what
# the prompt cache extends over; without this, a single huge dataframe print
# can fill the Layer B threshold by itself. Middle-snip (vs. CC's tail-truncate)
# preserves the informative head AND tail — both are signal in scientific
# output (df.head() at the top, summary/return value at the bottom; middle is
# typically repetition or progress lines). Set to 0 to disable capping.
TOOL_OUTPUT_CAP_CHARS = setting(
    "tool_output_cap_chars", env="ABA_TOOL_OUTPUT_CAP_CHARS", type="int",
    default=50000, category="tuning", weft_fate="keep",
    doc="Per-tool stdout/stderr cap (middle-snip) applied when a kernel result "
        "becomes a tool_result block. 0 disables capping.",
).get()

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

