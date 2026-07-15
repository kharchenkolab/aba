"""Settings routes — model/spec selection, LLM credential, analysis-environment
gate. Extracted from main.py (Item 2A.3). Domain-neutral (core.* only).

Pinning: the LLM model routes pin via Depends(optional_project) — project-scoped when
a project is open, install-wide (ABA_MODEL) when none. The credential + environment
routes are server/user-scoped (exempt in the pin gate), matching prior main.py behavior.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core import config as _cfg
from core.web.deps import optional_project

router = APIRouter()


@router.get("/api/specs/primary")
def specs_primary_list():
    """List all registered primary AgentSpecs. The frontend uses this
    to populate the new-chat "Backend" dropdown; the per-thread chooser
    on the chat screen reads it too. Empty list when nothing's
    registered (advisor-only contents)."""
    from core.runtime.agent import _SPECS, resolve_primary_spec_name
    active = resolve_primary_spec_name()
    items = []
    for name, spec in _SPECS.items():
        if spec.role != "primary":
            continue
        items.append({
            "name":            name,
            "model":           spec.model,
            "prompt_mode":     spec.prompt_mode,
            "tool_count":      (len(spec.tool_allowlist)
                                if "*" not in spec.tool_allowlist else None),
            "summary_budget":  spec.summary_budget_chars,
            "is_default":      name == active,
        })
    items.sort(key=lambda i: (not i["is_default"], i["name"]))
    return {"specs": items, "default": active}


class LlmSelectRequest(BaseModel):
    model: str


def _llm_current(pid: str | None) -> dict:
    from core.llm_catalog import spec_for_model, label_for_model, provider_for_model
    from core.config import current_model_for_project
    from core import projects
    m = current_model_for_project(pid)
    return {"model": m, "spec": spec_for_model(m), "label": label_for_model(m),
            "provider": provider_for_model(m),
            "pinned": bool(pid and projects.project_model(pid))}


@router.get("/api/settings/llm")
def settings_llm_get(_pid: str | None = Depends(optional_project)):
    """Model selector for Settings → Agent: the install-wide catalog (model→spec, from
    system_bundle/settings.yaml) plus what's active now. Project-OPTIONAL — with a
    project open, `current` is that project's model; with none (fresh install), it's the
    install-wide default. The user picks a model; the agent spec follows from the catalog."""
    from core.llm_catalog import llm_models
    return {"options": llm_models(), "current": _llm_current(_pid)}


@router.post("/api/settings/llm")
def settings_llm_set(req: LlmSelectRequest, _pid: str | None = Depends(optional_project)):
    """Select a model. With a project open → pin it on that project; with none → set the
    INSTALL-WIDE default (ABA_MODEL in config.env), which new projects inherit. Empty
    string clears the selection. Validated against the catalog; live on the next turn."""
    from core.llm_catalog import is_known_model
    from core import projects, config
    model = (req.model or "").strip()
    if model and not is_known_model(model):
        raise HTTPException(400, f"unknown model: {model!r}")
    if _pid:
        projects.set_project_model(_pid, model)   # per-project pin
    else:
        config.set_default_model(model)           # install-wide default (no project)
    return {"ok": True, "current": _llm_current(_pid)}


class LlmPingRequest(BaseModel):
    model: str = ""


@router.post("/api/settings/llm/ping")
def settings_llm_ping(req: LlmPingRequest, _pid: str | None = Depends(optional_project)):
    """Background model check for Settings → Agent: a cheap call confirming the current
    credential can actually run `model` (defaults to the project's model, or the
    install-wide default when no project). Returns {ok, detail?} so the UI can show
    ✓ Ready / ✗ reason at select time instead of failing on the first real message.
    Sync → FastAPI runs it off the event loop."""
    from core.model_ping import ping_model
    from core.config import current_model_for_project
    model = (req.model or "").strip() or current_model_for_project(_pid)
    return ping_model(model)


class CredentialRequest(BaseModel):
    credential: str
    provider: str = "anthropic"


@router.get("/api/settings/credential")
def settings_credential_get(provider: str = "anthropic"):
    """LLM credential status for Settings → Agent, per provider. Never echoes the
    secret — only the mode, a 4-char key suffix, OAuth expiry, and a `valid` flag the
    UI uses to decide between showing status+Change and showing the input."""
    from core import credentials, oauth
    st = credentials.status(provider if provider in ("anthropic", "openai") else "anthropic")
    # Whether subscription sign-in (OAuth) is available FOR THIS PROVIDER on this deployment,
    # so the UI hides the Subscription tab instead of offering a button that 400s. Per-provider
    # + mode-aware: a proxied/OOD deploy can offer Anthropic (paste flow) while hiding OpenAI
    # (localhost-callback flow the browser can't reach). See oauth.enabled().
    st["oauth_enabled"] = oauth.enabled(provider if provider in ("anthropic", "openai") else "anthropic")
    return st


@router.get("/api/settings/credential/any")
def settings_credential_any():
    """Whether ANY provider is connected — the app's first-run / skip-agent gate.
    The backend serves credential-less (data mgmt / viewers work); chat is gated on
    this. `{configured: bool, provider: str|None}`."""
    from core import credentials
    return credentials.any_configured()


@router.post("/api/settings/credential")
def settings_credential_set(req: CredentialRequest):
    """One field for both: auto-detects an API key vs a pasted token, VERIFIES it
    with the provider (a cheap call), then persists + goes live. HTTP 400 (with a
    message) on bad format or rejection — nothing is written unless it works."""
    from core import credentials
    prov = req.provider if req.provider in ("anthropic", "openai") else "anthropic"
    try:
        return credentials.set_credential(req.credential, provider=prov)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/api/settings/credential/oauth/start")
def settings_oauth_start(provider: str = "anthropic"):
    """Begin a subscription sign-in (Settings → Agent → Subscription). Returns
    {flow_id, authorize_url}; the UI opens the URL, the user signs in + copies the
    code, then POSTs it to .../oauth/submit. Reverse-engineered + feature-flagged
    (ABA_SUBSCRIPTION_OAUTH) — 400 with a clear message when off/unavailable."""
    from core import oauth
    prov = provider if provider in ("anthropic", "openai") else "anthropic"
    try:
        return oauth.start(prov)
    except ValueError as e:
        raise HTTPException(400, str(e))


class OAuthSubmitRequest(BaseModel):
    flow_id: str
    code: str


@router.post("/api/settings/credential/oauth/submit")
def settings_oauth_submit(req: OAuthSubmitRequest):
    """PASTE-flow (Anthropic): exchange the pasted sign-in code for a token, verify +
    persist it, and return the new credential status."""
    from core import oauth
    try:
        return oauth.submit(req.flow_id, req.code)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/settings/credential/oauth/poll")
def settings_oauth_poll(flow_id: str):
    """CALLBACK-flow (OpenAI/Codex): poll until the localhost callback captures the
    code and we exchange it. {state: pending|done|error, credential?, detail?}."""
    from core import oauth
    return oauth.poll(flow_id)


class EnvGateRequest(BaseModel):
    # auto (default) | off (Always show) | hard (Hide) | soft ; "" reverts to default
    env_gate: str = ""


@router.get("/api/settings/environment")
def settings_environment_get():
    """Backs the Settings → Analysis environment card: what this workspace can
    run (detected), the effective recipe-visibility policy, and its effect."""
    from core.exec.compute_env import env_profile
    from core.skills.loader import gate_counts, _env_gate_policy
    from core.config import get_user_pref
    policy = _env_gate_policy()
    return {
        "profile": env_profile(),
        "policy": policy,                                   # off | soft | hard (resolved)
        "user_pref": get_user_pref("discovery.env_gate") or "auto",
        "counts": gate_counts(policy),
        "options": ["auto", "off", "hard"],                 # card: Auto / Always / Hide
    }


@router.get("/api/settings/environment/prewarm")
def settings_environment_prewarm():
    """Staged-prewarm status for the ambient 'setting up…' pill + EnvironmentTab
    (lazy_env_init.md): the base-build stage and which module blocks are ready. Cheap
    — find_spec presence + a tools-env check, no subprocess imports."""
    import importlib.util as _u
    try:
        from core.exec.env_integrity import base_stage
        stage = base_stage()
    except Exception:  # noqa: BLE001
        stage = "ready"

    def _spec(m: str) -> bool:
        try:
            return _u.find_spec(m) is not None
        except Exception:  # noqa: BLE001
            return False
    try:
        from core.compute import base_env as _bev
        r_ready = _bev.active("r")   # R pack declared (realizes lazily on first use)
    except Exception:  # noqa: BLE001
        r_ready = False
    modules = [
        {"id": "single_cell", "label": "Single-cell (scanpy, anndata)", "ready": _spec("scanpy")},
        {"id": "deep_learning", "label": "Deep learning (PyTorch, scVI)",
         "ready": _spec("scvi") or _spec("torch")},
        {"id": "r_bioc", "label": "R / Bioconductor (Seurat, DESeq2)", "ready": r_ready},
    ]
    prewarm = _cfg.settings.env_prewarm.get().strip().lower()
    return {
        "prewarm": prewarm,
        "stage": stage,                                  # boot | completing | ready
        "setting_up": stage in ("boot", "completing"),
        "modules": modules,
    }


@router.post("/api/settings/environment")
def settings_environment_set(req: EnvGateRequest):
    """Set the user's recipe-visibility preference (user scope). Empty string
    reverts to auto/default. Takes effect on the next discovery call."""
    from core.config import set_user_pref
    from core.skills.loader import gate_counts, _env_gate_policy
    v = (req.env_gate or "").strip().lower()
    if v and v not in ("auto", "off", "soft", "hard"):
        raise HTTPException(400, f"env_gate must be auto|off|soft|hard, got {v!r}")
    set_user_pref("discovery.env_gate", v)                  # "" clears the pin
    policy = _env_gate_policy()
    return {"ok": True, "policy": policy, "counts": gate_counts(policy)}
