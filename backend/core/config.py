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


def _coerce_truthy_presence(raw):
    """Any non-empty value → True (matches ``bool(os.environ.get(k))``; note "0"
    is truthy here, exactly as the historical reads treated it)."""
    return bool(raw)


def _coerce_lower_strip(raw):
    return str(raw).strip().lower()


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
                 "empty_is_unset", "_cache", "_has_cache")

    def __init__(self, *, name, env, type, default, coerce, resolver, category,
                 doc, branches, deploy_injected, secret, weft_fate, reduction,
                 enum, resolve_mode, empty_is_unset):
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
        self.empty_is_unset = empty_is_unset
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
            # An empty env value is treated as unset when empty_is_unset (the
            # `os.environ.get(k) or default` idiom), or for numeric/path types
            # (historically `int(os.environ.get(k, "5"))` would crash on ""). A
            # single-key str/csv setting keeps "" as a real value (matching the
            # `os.environ.get(k, "")` idiom).
            if raw == "" and (self.empty_is_unset or self.type not in ("str", "csv")):
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

    def is_set(self) -> bool:
        """True iff an env key for this setting is present with a truthy (non-empty)
        value — exactly `any(os.environ.get(k) for k in env_keys)`. Lets callers
        replicate the old `if os.environ.get(k):` guard (e.g. SIF binds) without
        reading os.environ directly."""
        return any(os.environ.get(k) for k in self.env_keys)

    def __repr__(self):
        return f"Setting({self.name!r}, env={self.env_keys})"


def setting(name, *, env=None, type="str", default=None, coerce=None,
            resolver=None, category="", doc="", branches=False,
            deploy_injected=False, secret=False, weft_fate="keep",
            reduction="keep", enum=None, resolve="lazy", empty_is_unset=False):
    """Declare a setting once; register it and return the ``Setting`` accessor.

    Callers read the live value via the returned object's ``.get()`` or via
    ``settings.<name>.get()``. Scalar back-compat module constants bind the frozen
    ``.get()`` value; path tiers go through ``_path_setting`` (see below).

    ``empty_is_unset`` — an empty env value falls through to the next key/default
    (the ``os.environ.get(k) or default`` idiom); needed for precedence lists."""
    if name in _REGISTRY:
        raise ValueError(f"setting {name!r} already declared")
    s = Setting(name=name, env=env, type=type, default=default, coerce=coerce,
                resolver=resolver, category=category, doc=doc, branches=branches,
                deploy_injected=deploy_injected, secret=secret,
                weft_fate=weft_fate, reduction=reduction, enum=enum,
                resolve_mode=resolve, empty_is_unset=empty_is_unset)
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
PROJECTS_DIR = _path_setting(
    "projects_dir", "ABA_PROJECTS_DIR",
    lambda: _resolve_under_runtime("ABA_PROJECTS_DIR", "projects"),
    doc="Per-project consolidated roots (projects/<pid>/).")
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


# ═══════════════════════════════════════════════════════════════════════════
# Full env surface (env_reorg Phase 3)
# ---------------------------------------------------------------------------
# Every ABA_* setting the backend reads, declared once. Consumers read via
# `config.settings.<name>.get()`. Grouped by category; each carries weft_fate
# (weft's move/delete checklist) and reduction (the fewer-better-vars plan).
# Callers keep any surrounding transform (.strip()/.lower()/`or <dynamic>`);
# static defaults are baked into the setting.
# ═══════════════════════════════════════════════════════════════════════════

# ── Paths / deploy plumbing ──────────────────────────────────────────────────
# ABA_HOME is declared as a RAW str (None when unset) so callers that rely on the
# "explicitly set?" semantics (llm._oauth_store_path) keep working; aba_home()
# applies the ~/.aba fallback for the common "default to ~/.aba" reads.
setting("home_dir", env="ABA_HOME", type="str", default=None, category="paths",
        deploy_injected=True, weft_fate="keep",
        doc="Install home ($ABA_HOME): config.env, oauth store, vendor. None → ~/.aba.")


def aba_home() -> Path:
    """The install home as a plain Path (live), with the ~/.aba fallback applied.
    Preferred over reading ABA_HOME directly."""
    return Path(settings.home_dir.get() or (Path.home() / ".aba"))


setting("tools_dir", env="ABA_TOOLS_DIR", type="str", default=None,
        category="paths", weft_fate="move:envspec",
        doc="Override for the materialized-tools dir (else derived under ENVS_DIR).")
setting("refsources_dir", env="ABA_REFSOURCES_DIR", type="str", default=None,
        category="paths", weft_fate="move:site",
        doc="Override for the reference-sources catalog dir.")
setting("frontend_dist", env="ABA_FRONTEND_DIST", type="str", default=None,
        category="paths", deploy_injected=True, weft_fate="keep",
        doc="Built frontend dist dir served by the backend.")
setting("pagoda3_dist", env="ABA_PAGODA3_DIST", type="str", default=None,
        category="paths", weft_fate="move:site",
        doc="pagoda3 viewer dist dir (else derived under $ABA_HOME/vendor).")
setting("turn_log_dir", env="ABA_TURN_LOG_DIR", type="str", default="/tmp/aba_turnlog",
        category="paths", weft_fate="keep",
        doc="Directory for per-turn structured logs.")
setting("raw_request_dir", env="ABA_RAW_REQUEST_DIR", type="str",
        default="/tmp/aba_llm_sent", category="paths", weft_fate="keep",
        doc="Debug dump dir for raw LLM requests (diagnostics only).")
setting("share_dir", env="ABA_SHARE", type="str", default=None, category="paths",
        deploy_injected=True, weft_fate="move:site",
        doc="Shared install tree ($ABA_SHARE) for immutable releases; unset on personal/slim.")
setting("release_id", env="ABA_RELEASE_ID", type="str", default=None,
        category="paths", deploy_injected=True, weft_fate="move:site",
        doc="Active release id under $ABA_SHARE/releases (else resolve_current()).")

# ── Container / offload / modules deploy wiring (mostly move:site under weft) ──
setting("sif", env="ABA_SIF", type="str", default=None, category="deploy",
        deploy_injected=True, weft_fate="move:site",
        doc="Path to the fat/slim SIF image used to wrap jobs.")
setting("job_wrap", env="ABA_JOB_WRAP", type="str", default="", category="deploy",
        branches=True, weft_fate="move:site",
        doc="Job wrapper mode ('sif' → run jobs via apptainer exec <SIF>).")
setting("base_lock", env="ABA_BASE_LOCK", type="str", default=None,
        category="deploy", weft_fate="move:envspec",
        doc="Path to the base environment lock (integrity check).")
setting("offload_python", env="ABA_OFFLOAD_PYTHON", type="str", default=None,
        category="deploy", weft_fate="retire", reduction="derive:sif",
        doc="Python interpreter for offloaded (sbatch) jobs; else sys.executable.")
setting("offload_backend_dir", env="ABA_OFFLOAD_BACKEND_DIR", type="str",
        default=None, category="deploy", weft_fate="retire", reduction="derive:sif",
        doc="Backend dir made importable in offloaded jobs; else the live backend dir.")
setting("apptainer_tmpdir", env="ABA_APPTAINER_TMPDIR", type="str", default=None,
        category="deploy", weft_fate="move:site",
        doc="TMPDIR for apptainer/singularity build+run scratch.")
setting("module_binds", env="ABA_MODULE_BINDS", type="str", default="",
        category="deploy", weft_fate="move:site",
        doc="Space-separated bind mounts injected when wrapping jobs in the SIF.")
setting("module_init", env="ABA_MODULE_INIT", type="str", default=None,
        category="deploy", weft_fate="move:site",
        doc="Lmod init snippet path for module-based nextflow/tool execution.")
setting("lmod_init", env="ABA_LMOD_INIT", type="str", default="", category="deploy",
        weft_fate="move:site",
        doc="Lmod init script path (else from site config init_path).")
setting("modules_enabled", env="ABA_MODULES_ENABLED", type="str", default=None,
        category="deploy", branches=True, weft_fate="move:site",
        doc="'0' disables the environment-modules integration.")
setting("modules_eager", env="ABA_MODULES_EAGER", type="str", default="",
        category="deploy", weft_fate="move:site",
        doc="Eagerly materialize module manifests at startup (fat-SIF baked artifacts).")
setting("accelerator", env="ABA_ACCELERATOR", type="str", default="",
        category="deploy", branches=True, weft_fate="move:site", reduction="derive:gpu-probe",
        doc="Accelerator hint ('cuda' → CUDA-aware paths); else CPU / probe-derived.")

# ── DB / process modes ───────────────────────────────────────────────────────
setting("db_path", env="ABA_DB_PATH", type="str", default=None, category="mode",
        branches=True, weft_fate="keep", reduction="merge:db_path",
        doc="Explicit workspace DB path → SINGLE mode (tests / single-user).")
setting("db_path_override", env="ABA_DB_PATH_OVERRIDE", type="str", default=None,
        category="mode", branches=True, weft_fate="keep", reduction="merge:db_path",
        doc="e2e-harness DB override (also triggers SINGLE mode). Alias of db_path.")
setting("runtime_override", env="ABA_RUNTIME_OVERRIDE", type="str", default=None,
        category="mode", branches=True, weft_fate="keep",
        enum=("direct", "sdk", "fake", "openai"),
        doc="Force the LLM runtime backend for the process (direct/sdk/fake/openai).")
setting("disabled_tools", env="ABA_DISABLED_TOOLS", type="csv", default=(),
        category="mode", branches=True, weft_fate="keep",
        doc="Comma-separated global tool kill-switch (layered under agent allowlists).")
setting("version", env="ABA_VERSION", type="str", default="dev", category="mode",
        weft_fate="keep", doc="Deployed ABA version label (provenance stamp).")

# ── Model / LLM (Reasoning plane — aba-owned; model resolver unified in Phase 7) ─
setting("primary_model", env=["ABA_PRIMARY_MODEL", "ABA_MODEL"], type="str",
        default=None, empty_is_unset=True, category="model", branches=True,
        weft_fate="keep", reduction="merge:model",
        doc="Targeted primary-model override (ABA_PRIMARY_MODEL, else ABA_MODEL).")
setting("primary_spec", env="ABA_PRIMARY_SPEC", type="str", default="",
        empty_is_unset=True, category="model", weft_fate="keep",
        doc="Force a specific agent spec for the primary lane.")
setting("summary_model", env="ABA_SUMMARY_MODEL", type="str",
        default="claude-haiku-4-5-20251001", empty_is_unset=True, category="model",
        weft_fate="keep", reduction="merge:model",
        doc="Model used for Tier-2 history summarization.")
setting("max_tokens", env="ABA_MAX_TOKENS", type="int", default=16000,
        category="model", weft_fate="keep",
        doc="Max output tokens for primary LLM calls.")

# ── OpenAI-compatible provider ───────────────────────────────────────────────
setting("openai_api_key", env="ABA_OPENAI_API_KEY", type="str", default=None,
        category="credentials", secret=True, weft_fate="keep", reduction="merge:openai",
        doc="OpenAI-compatible API key (ABA-scoped).")
setting("openai_base_url", env="ABA_OPENAI_BASE_URL", type="str", default=None,
        category="credentials", weft_fate="keep", reduction="merge:openai",
        doc="OpenAI-compatible base URL (else provider default).")
setting("openai_account_id", env="ABA_OPENAI_ACCOUNT_ID", type="str", default=None,
        category="credentials", weft_fate="keep", reduction="merge:openai",
        doc="ChatGPT-Account-Id for the Codex subscription backend.")
setting("openai_model", env="ABA_OPENAI_MODEL", type="str", default=None,
        category="model", weft_fate="keep", reduction="merge:openai",
        doc="Default model for the OpenAI-compatible runtime (else caller default).")
setting("openai_enable_thinking", env="ABA_OPENAI_ENABLE_THINKING", type="str",
        default="", category="model", weft_fate="keep", reduction="merge:openai",
        doc="Opt into 'thinking' for the OpenAI runtime (1/true/yes/on).")
setting("openai_tool_result_framing", env="ABA_OPENAI_TOOL_RESULT_FRAMING",
        type="str", default="none", category="model", weft_fate="keep",
        reduction="merge:openai",
        doc="How tool results are framed for the OpenAI runtime.")
setting("openai_oauth_client_id", env="ABA_OPENAI_OAUTH_CLIENT_ID", type="str",
        default="app_EMoamEEZ73f0CkXaXp7hrann", category="credentials",
        weft_fate="keep", reduction="merge:openai",
        doc="OAuth client id for the Codex subscription sign-in.")

# ── Credentials (mode selectors; the secret STORE unification is Phase 7) ─────
setting("llm_credential", env="ABA_LLM_CREDENTIAL", type="str", default=None,
        category="credentials", weft_fate="keep",
        doc="Credential MODE selector (e.g. 'oauth_cc'/'apikey') — not the secret itself.")
setting("subscription_oauth", env="ABA_SUBSCRIPTION_OAUTH", type="str", default="",
        category="credentials", weft_fate="keep",
        doc="Gates the subscription (Claude.ai/Codex) sign-in flow.")

# ── Behavior toggles / feature flags ─────────────────────────────────────────
setting("advisors_enabled", env="ABA_ADVISORS_ENABLED", type="bool", default=False,
        category="behavior", branches=True, weft_fate="keep",
        doc="Enable the advisor sub-agents pass.")
setting("preexec_veto", env="ABA_PREEXEC_VETO", type="str", default="on",
        empty_is_unset=True, category="behavior", branches=True, weft_fate="keep",
        doc="Pre-exec safety veto; 'off' disables it.")
setting("feed_log", env="ABA_FEED_LOG", type="str", default="on", category="behavior",
        weft_fate="keep", doc="Feedback event logging; 'off' disables it.")
setting("data_summary", env="ABA_DATA_SUMMARY", type="str", default="on",
        empty_is_unset=True, category="behavior", branches=True, weft_fate="keep",
        doc="Inject the data-summary prompt block; 'off' disables it.")
setting("prompt_arm", env="ABA_PROMPT_ARM", type="str", default="control",
        empty_is_unset=True, category="behavior", branches=True, weft_fate="keep",
        doc="A/B prompt arm selector (default 'control').")
setting("discovery_env_gate", env="ABA_DISCOVERY_ENV_GATE", type="str", default=None,
        category="behavior", branches=True, weft_fate="keep",
        reduction="relocate:userpref",
        doc="Env-gate for capability discovery (also a user preference).")
setting("recovery_disabled", env="ABA_RECOVERY_DISABLED", type="bool", default=False,
        coerce=_coerce_truthy_presence, empty_is_unset=True, category="behavior",
        branches=True, weft_fate="keep",
        doc="Any value disables the scribe recovery journal.")
setting("debug_timing", env="ABA_DEBUG_TIMING", type="bool", default=False,
        coerce=_coerce_truthy_presence, empty_is_unset=True, category="behavior",
        weft_fate="keep", doc="Emit per-stage timing diagnostics.")
setting("env_prewarm", env="ABA_ENV_PREWARM", type="str", default="eager",
        empty_is_unset=True, category="behavior", branches=True, weft_fate="revisit",
        doc="Environment prewarm policy ('eager'/'lazy'/…).")

# ── Experimental gates (Phase-7 resolve-flag targets) ────────────────────────
setting("experimental_prescriptive_search_skills",
        env="ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS", type="bool", default=False,
        coerce=_coerce_truthy_presence, empty_is_unset=True, category="experimental",
        branches=True, weft_fate="keep", reduction="resolve-flag",
        doc="Experimental: prescriptive search-skills behavior.")
setting("experimental_fetch_recipe", env="ABA_EXPERIMENTAL_FETCH_RECIPE",
        type="bool", default=False, coerce=_coerce_truthy_presence,
        empty_is_unset=True, category="experimental", branches=True, weft_fate="keep",
        reduction="resolve-flag", doc="Experimental: fetch-recipe discovery path.")
setting("experimental_discovery_directive", env="ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE",
        type="bool", default=False, coerce=_coerce_truthy_presence,
        empty_is_unset=True, category="experimental", branches=True, weft_fate="keep",
        reduction="resolve-flag", doc="Experimental: discovery directive prompt block.")
setting("experimental_ablate_blocks", env="ABA_EXPERIMENTAL_ABLATE_BLOCKS",
        type="csv", default=(), category="experimental", branches=True,
        weft_fate="keep", reduction="resolve-flag",
        doc="Experimental: comma-separated prompt blocks to ablate.")

# ── Numeric / tuning knobs ───────────────────────────────────────────────────
setting("kernel_threads", env="ABA_KERNEL_THREADS", type="str", default="",
        category="tuning", weft_fate="revisit", reduction="merge:kernel",
        doc="Override thread count for kernels (else CPU-derived).")
setting("kernel_cancel_grace_s", env="ABA_KERNEL_CANCEL_GRACE_S", type="float",
        default=3.0, category="tuning", weft_fate="revisit", reduction="merge:kernel",
        doc="Grace period (s) before force-killing a cancelled kernel cell.")
setting("cpu_limit", env="ABA_CPU_LIMIT", type="str", default="", category="tuning",
        weft_fate="move:site", doc="Override detected CPU limit (else cgroup/os probe).")
setting("import_harvest_cap", env="ABA_IMPORT_HARVEST_CAP", type="int", default=40,
        category="tuning", weft_fate="keep",
        doc="Max symbols harvested from an import for the tool catalog.")
setting("mcp_registry_url", env="ABA_MCP_REGISTRY_URL", type="str", default=None,
        category="tuning", weft_fate="keep",
        doc="MCP registry URL override (else the built-in default).")
setting("feedback_email", env="ABA_FEEDBACK_EMAIL", type="str",
        default="pk.restricted@gmail.com", category="tuning", weft_fate="keep",
        doc="Destination address for in-app feedback.")

# ── R runtime knobs (mostly → weft EnvSpec) ──────────────────────────────────
setting("r_plot_res", env="ABA_R_PLOT_RES", type="int", default=120,
        category="tuning", weft_fate="move:envspec", reduction="merge:r",
        doc="R plot DPI (floored at 40 by the caller).")
setting("r_future_plan", env="ABA_R_FUTURE_PLAN", type="str", default="sequential",
        category="tuning", weft_fate="move:envspec", reduction="merge:r",
        doc="future::plan for R (sequential/multicore/…).")
setting("r_future_globals_maxsize", env="ABA_R_FUTURE_GLOBALS_MAXSIZE", type="str",
        default=str(8 * 1024 ** 3), category="tuning", weft_fate="move:envspec",
        reduction="merge:r", doc="future.globals.maxSize for R in bytes (default 8 GiB).")
setting("r_build_jobs", env="ABA_R_BUILD_JOBS", type="str", default=None,
        category="tuning", weft_fate="move:envspec", reduction="merge:r",
        doc="Parallel jobs for R source builds (MAKEFLAGS -j).")
setting("r_ppm_base", env="ABA_R_PPM_BASE", type="str", default=None,
        category="tuning", weft_fate="move:envspec", reduction="merge:r_ppm",
        doc="Posit Package Manager base URL (else built-in default).")
setting("r_ppm_distro", env="ABA_R_PPM_DISTRO", type="str", default=None,
        category="tuning", weft_fate="move:envspec", reduction="merge:r_ppm",
        doc="PPM binary distro tag (else auto-detected).")
setting("r_ppm_snapshot", env="ABA_R_PPM_SNAPSHOT", type="str", default="latest",
        category="tuning", weft_fate="move:envspec", reduction="merge:r_ppm",
        doc="PPM snapshot date/tag ('latest' or YYYY-MM-DD).")

# ── Cluster / jobs (weft owns placement/exec → mostly retire) ─────────────────
setting("batch_submitter", env="ABA_BATCH_SUBMITTER", type="str", default="local",
        coerce=_coerce_lower_strip, empty_is_unset=True, category="cluster",
        branches=True, weft_fate="retire",
        doc="Batch backend: 'local' or 'slurm'.")
setting("hpc_config", env="ABA_HPC_CONFIG", type="str", default=None,
        category="cluster", weft_fate="retire", reduction="relocate:hpc.yaml",
        doc="Path to hpc.yaml compute-topology override (else $ABA_HOME/hpc.yaml).")
setting("slurm_mem_frac", env="ABA_SLURM_MEM_FRAC", type="float", default=0.85,
        category="cluster", weft_fate="retire",
        doc="Fraction of node memory an inline job may use before offloading.")
setting("slurm_walltime_frac", env="ABA_SLURM_WALLTIME_FRAC", type="float",
        default=0.8, category="cluster", weft_fate="retire",
        doc="Fraction of walltime an inline job may use before offloading.")
setting("inline_stall_min", env="ABA_INLINE_STALL_MIN", type="float", default=20.0,
        category="cluster", weft_fate="retire", reduction="merge:inline",
        doc="Whole-run silence budget (min) before an inline run is deemed stalled.")
setting("inline_stall_cpu_sample_s", env="ABA_INLINE_STALL_CPU_SAMPLE_S",
        type="float", default=3.0, category="cluster", weft_fate="retire",
        reduction="merge:inline", doc="CPU sampling window (s) for stall detection.")
setting("inline_auto_max_cores", env="ABA_INLINE_AUTO_MAX_CORES", type="float",
        default=8.0, category="cluster", weft_fate="retire", reduction="merge:inline",
        doc="Max cores an auto-inline job may claim before offloading.")
setting("inline_auto_max_mem_gb", env="ABA_INLINE_AUTO_MAX_MEM_GB", type="float",
        default=32.0, category="cluster", weft_fate="retire", reduction="merge:inline",
        doc="Max memory (GB) an auto-inline job may claim before offloading.")

# ── Nextflow (self-contained compute subsystem; retire/move:site under weft) ──
setting("nextflow_module", env="ABA_NEXTFLOW_MODULE", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Lmod module providing nextflow (else site config).")
setting("nextflow_profiles", env="ABA_NEXTFLOW_PROFILES", type="str", default=None,
        category="nextflow", weft_fate="retire", reduction="merge:nextflow",
        doc="Comma-separated nextflow profiles (else site config).")
setting("nextflow_cachedir", env="ABA_NEXTFLOW_CACHEDIR", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Singularity cache dir for nextflow (else site config).")
setting("nextflow_workdir", env="ABA_NEXTFLOW_WORKDIR", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Nextflow work dir root (else site config).")
setting("nextflow_config", env="ABA_NEXTFLOW_CONFIG", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Extra nextflow -c config file (else site config).")
setting("nextflow_bin", env="ABA_NEXTFLOW_BIN", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Dir/launcher for a self-installed nextflow prepended to PATH.")
setting("nextflow_home", env="ABA_NEXTFLOW_HOME", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="Persistent NXF_HOME (plugins/assets); else per-run scratch.")
setting("nextflow_java_home", env="ABA_NEXTFLOW_JAVA_HOME", type="str", default=None,
        category="nextflow", weft_fate="move:site", reduction="merge:nextflow",
        doc="JAVA_HOME for the nextflow head (Java ≥17).")
setting("nextflow_execution", env="ABA_NEXTFLOW_EXECUTION", type="str", default=None,
        empty_is_unset=True, category="nextflow", branches=True, weft_fate="retire",
        reduction="merge:nextflow",
        doc="Nextflow execution mode ('slurm'/'local'; else site config).")
setting("nextflow_local_max_cores", env="ABA_NEXTFLOW_LOCAL_MAX_CORES", type="float",
        default=None, category="nextflow", weft_fate="retire", reduction="merge:nextflow",
        doc="Ceiling on cores for local nextflow execution (else 36).")
setting("nextflow_local_max_mem_gb", env="ABA_NEXTFLOW_LOCAL_MAX_MEM_GB",
        type="float", default=None, category="nextflow", weft_fate="retire",
        reduction="merge:nextflow",
        doc="Ceiling on memory (GB) for local nextflow execution (else 180).")
# Per-field head/local Slurm footprint overrides (dict-driven in nextflow.py).
for _tier in ("head", "local"):
    for _field, _ty in (("cores", "int"), ("mem_gb", "int"), ("walltime_h", "int"),
                        ("qos", "str"), ("partition", "str")):
        setting(f"nextflow_{_tier}_{_field}",
                env=f"ABA_NEXTFLOW_{_tier.upper()}_{_field.upper()}", type=_ty,
                default=None, category="nextflow", weft_fate="retire",
                reduction="merge:nextflow",
                doc=f"Per-field {_tier} Slurm override for nextflow ({_field}).")

# ── Bundle scope resolution (read via a passed env dict in scope_resolver;
#    declared for `aba doctor` visibility). ────────────────────────────────────
setting("site_config", env="ABA_SITE_CONFIG", type="str", default=None,
        category="bundle", weft_fate="keep",
        doc="Path to the deployment site.yaml (scopes, credentials, paths).")
for _scope in ("system", "institution", "lab", "user"):
    setting(f"{_scope}_bundle", env=f"ABA_{_scope.upper()}_BUNDLE", type="str",
            default=None, category="bundle", weft_fate="keep",
            doc=f"Override path for the {_scope} bundle scope.")
setting("composed_bundle_path", env="ABA_COMPOSED_BUNDLE_PATH", type="str",
        default=None, category="bundle", weft_fate="keep",
        doc="Precomposed effective-bundle path (future marker).")
setting("group", env="ABA_GROUP", type="str", default=None, category="bundle",
        weft_fate="keep", doc="Group/lab id for group-scoped bundle + credentials.")
setting("state_dir", env="ABA_STATE_DIR", type="str", default=None, category="bundle",
        weft_fate="keep", doc="User state dir (else $ABA_HOME/state or site config).")
setting("scratch_dir", env="ABA_SCRATCH", type="str", default=None, category="bundle",
        weft_fate="keep", doc="Optional user scratch dir (else site config).")


# current_project_id moved to core.projects (burn-down #1 — config is a leaf,
# must not import projects).

