"""Settings routes — model/spec selection, LLM credential, analysis-environment
gate. Extracted from main.py (Item 2A.3). Domain-neutral (core.* only).

Pinning: the per-project LLM model routes pin via Depends(require_project); the
credential + environment routes are server/user-scoped (exempt in the pin gate),
matching their prior main.py behavior.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.web.deps import require_project

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


def _llm_current(pid: str) -> dict:
    from core.llm_catalog import spec_for_model, label_for_model, provider_for_model
    from core.config import current_model_for_project
    from core import projects
    m = current_model_for_project(pid)
    return {"model": m, "spec": spec_for_model(m), "label": label_for_model(m),
            "provider": provider_for_model(m),
            "pinned": bool(projects.project_model(pid))}


@router.get("/api/settings/llm")
def settings_llm_get(_pid: str = Depends(require_project)):
    """Model selector for the CURRENT project (Settings → LLM): the install-wide
    catalog (model→spec, from system_bundle/settings.yaml) plus what's active now.
    The user picks a model; the agent spec follows from the catalog."""
    from core.llm_catalog import llm_models
    return {"options": llm_models(), "current": _llm_current(_pid)}


@router.post("/api/settings/llm")
def settings_llm_set(req: LlmSelectRequest, _pid: str = Depends(require_project)):
    """Pin a model on the current project. Empty string clears the pin (revert to
    the global/bundle default). Validated against the catalog; takes effect on the
    next turn (resolution is live)."""
    from core.llm_catalog import is_known_model
    from core import projects
    model = (req.model or "").strip()
    if model and not is_known_model(model):
        raise HTTPException(400, f"unknown model: {model!r}")
    projects.set_project_model(_pid, model)
    return {"ok": True, "current": _llm_current(_pid)}


class LlmPingRequest(BaseModel):
    model: str = ""


@router.post("/api/settings/llm/ping")
def settings_llm_ping(req: LlmPingRequest, _pid: str = Depends(require_project)):
    """Background model check for Settings → Agent: a cheap call confirming the current
    credential can actually run `model` (defaults to the project's model). Returns
    {ok, detail?} so the UI can show ✓ Ready / ✗ reason at select time instead of
    failing on the first real message. Sync → FastAPI runs it off the event loop."""
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
    from core import credentials
    return credentials.status(provider if provider in ("anthropic", "openai") else "anthropic")


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
