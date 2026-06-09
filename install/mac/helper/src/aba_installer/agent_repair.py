"""Agent-driven install repair (Tier-0, phase 1).

The deterministic playbook (install.yml) is the happy-path spine. When a step
fails, instead of dead-ending we hand the failure to Claude Code — run headless
(`claude -p`) with a scoped tool allowlist — to diagnose the *system* problem
(quarantined binary, missing tool, proxy/network, permissions, …), fix it, and
then we re-run the step. Off by default; enabled via ABA_INSTALL_AGENT_REPAIR.

Why Claude Code rather than a bespoke agent loop: it IS the agent harness
(shell + files + permissions + streaming), installs Node-free, and manages its
own auth. We bootstrap/locate the `claude` binary and drive it over a subprocess
boundary, keeping this module thin and fully unit-testable via an injectable
`runner` (no network/real CLI needed in tests)."""
from __future__ import annotations
import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from aba_installer.paths import aba_home, config_env

RECIPE_PATH = Path(__file__).with_name("repair_recipe.md")

# `dontAsk` permission mode denies anything not in this allowlist (the safety
# boundary for an unattended agent). Curated for common macOS install fixes;
# deliberately excludes `sudo`. Tune as we learn real failure modes.
DEFAULT_ALLOWED_TOOLS = [
    "Read", "Write",
    "Bash(ls *)", "Bash(cat *)", "Bash(echo *)", "Bash(mkdir *)", "Bash(chmod *)",
    "Bash(xattr *)",            # de-quarantine downloaded binaries (Gatekeeper)
    "Bash(curl *)",             # re-download / mirror
    "Bash(xcode-select *)",     # detect (cannot silently install) the CLT
    "Bash(git *)",
    "Bash(tar *)", "Bash(unzip *)",
    "Bash(file *)", "Bash(uname *)", "Bash(which *)", "Bash(networksetup *)",
]


@dataclass
class RepairOutcome:
    attempted: bool
    ok: bool                     # claude exited cleanly (≠ proof the step is fixed; the re-run is the judge)
    diagnosis: str = ""
    reason: str = ""             # why we couldn't attempt, if attempted is False
    raw: dict = field(default_factory=dict)


# Substrings that signal claude couldn't authenticate (no session, expired token,
# wrong API key). Substring match is intentionally permissive — better to over-skip
# than to retry endlessly on a doomed credential. Anchored to the messages we see
# from Claude Code 2.1.x; revisit if a structured error code becomes available in
# --output-format json.
_AUTH_FAIL_SIGNATURES = (
    "not logged in",
    "please run /login",
    "invalid api key",
    "authentication_error",
    "unauthorized",
    "401",
)


def _looks_like_auth_failure(text: str) -> bool:
    if not text:
        return False
    lower = text.lower()
    return any(s in lower for s in _AUTH_FAIL_SIGNATURES)


_NO_CREDENTIAL_MSG = (
    "ABA isn't signed in yet — agent-repair is unavailable until sign-in "
    "completes. This step's failure needs a manual fix."
)
_AUTH_FAILED_MSG = (
    "Claude Code reported an auth failure — re-sign-in to ABA and re-run "
    "the install."
)


# ─── system probe ───────────────────────────────────────────────────────────
def _has_xcode_clt() -> bool:
    try:
        return subprocess.run(["xcode-select", "-p"],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def probe_system() -> dict:
    """Lightweight, fast facts to orient the repair agent."""
    def have(cmd: str) -> bool:
        return shutil.which(cmd) is not None
    return {
        "os": "macOS",
        "macos_version": platform.mac_ver()[0] or "unknown",
        "arch": platform.machine(),
        "shell": os.environ.get("SHELL", ""),
        "has_git": have("git"),
        "has_curl": have("curl"),
        "has_xcode_clt": _has_xcode_clt(),
        "aba_home": os.environ.get("ABA_HOME", ""),
    }


# ─── ABA credential pass-through ────────────────────────────────────────────
# Reuse ABA's existing credential (the one captured by the helper UI's sign-in)
# so `claude -p` authenticates without depending on ~/.claude. Both env var names
# below — CLAUDE_CODE_OAUTH_TOKEN and ANTHROPIC_API_KEY — are read natively by
# Claude Code, so the merged env just works; no translation needed.
#
# Parsing + refresh logic mirrors auth.py:_parse_config_env and
# backend/core/llm.py:_refresh_oauth respectively. Duplicated here to keep this
# module dep-light (no fastapi pull-in) and self-contained for the offline cases
# the playbook runs through.

_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"   # mirror auth.py
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_USER_AGENT = "aba-installer-repair (kharchenkolab/aba)"
_OAUTH_REFRESH_SKEW = 60   # refresh when within this many seconds of expiry


def _parse_config_env(text: str) -> dict[str, str]:
    """Parse `export K=V` lines from $ABA_HOME/config.env. Tolerant of quotes
    and comments. Mirrors auth.py:_parse_config_env."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        body = line[len("export "):] if line.startswith("export ") else line
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _read_config_env() -> dict[str, str]:
    cfg = config_env()
    if not cfg.exists():
        return {}
    try:
        return _parse_config_env(cfg.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _oauth_store_path() -> Path:
    return aba_home() / "oauth.json"


def _load_oauth_store() -> Optional[dict]:
    p = _oauth_store_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return None


def _save_oauth_store(store: dict) -> None:
    p = _oauth_store_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / "oauth.json.tmp"
        tmp.write_text(json.dumps(store))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)        # atomic — won't corrupt a concurrent reader
    except Exception:  # noqa: BLE001
        pass


def _refresh_oauth_token(store: dict) -> Optional[str]:
    """Exchange a refresh_token for a fresh access token; persist the rotated
    pair and return the new access token. Mirrors backend/core/llm.py
    _refresh_oauth; returns None on failure."""
    import urllib.request   # local — avoids cost on the happy path
    import urllib.error     # noqa: F401  (imported for the except clause)
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": store["refresh_token"],
        "client_id": _OAUTH_CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        _OAUTH_TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _OAUTH_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception:  # noqa: BLE001
        return None
    at = data.get("access_token")
    if not at:
        return None
    _save_oauth_store({
        "access_token": at,
        # Refresh tokens rotate (single-use); keep the new one, fall back to the
        # old only if the response omitted it.
        "refresh_token": data.get("refresh_token") or store.get("refresh_token"),
        "expires_at": time.time() + (data.get("expires_in") or 3600),
    })
    return at


def _aba_credential_env() -> dict[str, str]:
    """Env vars carrying ABA's existing credential to `claude -p`.

    Priority: (1) refreshable OAuth store ($ABA_HOME/oauth.json) from the browser
    flow — auto-refreshed near expiry; (2) CLAUDE_CODE_OAUTH_TOKEN from
    config.env (pasted/setup-token path — long-lived); (3) ANTHROPIC_API_KEY
    from config.env. Returns {} when ABA is not yet signed in (the bg install
    starts before sign-in, so this is a legitimate state, not an error)."""
    # (1) Refreshable OAuth store.
    store = _load_oauth_store()
    if store and store.get("access_token"):
        exp = store.get("expires_at")
        if not exp or time.time() < exp - _OAUTH_REFRESH_SKEW:
            return {"CLAUDE_CODE_OAUTH_TOKEN": store["access_token"]}
        if store.get("refresh_token"):
            new_tok = _refresh_oauth_token(store)
            if new_tok:
                return {"CLAUDE_CODE_OAUTH_TOKEN": new_tok}
        # Expired with no usable refresh path → fall through to config.env.

    # (2) + (3) Static credentials from config.env.
    cfg = _read_config_env()
    tok = (cfg.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
    if tok:
        return {"CLAUDE_CODE_OAUTH_TOKEN": tok}
    key = (cfg.get("ANTHROPIC_API_KEY") or "").strip()
    if key:
        return {"ANTHROPIC_API_KEY": key}
    return {}


# ─── claude binary discovery ────────────────────────────────────────────────
def claude_path() -> Optional[str]:
    """Locate a usable `claude`: $ABA_HOME/bin, the native installer's
    ~/.local/bin, then PATH. Returns the path or None."""
    candidates = []
    home = os.environ.get("ABA_HOME")
    if home:
        candidates.append(Path(home) / "bin" / "claude")
    candidates.append(Path.home() / ".local" / "bin" / "claude")
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return shutil.which("claude")


# Node-free native installer (installs ~/.local/bin/claude). DISABLE_AUTOUPDATER
# so it can't self-update mid-install.
CLAUDE_INSTALL_CMD = "curl -fsSL https://claude.ai/install.sh | bash"


def _default_installer() -> None:
    subprocess.run(CLAUDE_INSTALL_CMD, shell=True, check=True, timeout=300,
                   env={**os.environ, "DISABLE_AUTOUPDATER": "1"})


def ensure_claude(*, installer: Optional[Callable] = None,
                  on_event: Optional[Callable[[str, dict], None]] = None) -> Optional[str]:
    """A usable `claude` path, installing the native CLI into the user's space if
    absent. `installer` is injectable for tests (default: the curl|bash native
    install). Returns None if it still isn't available after install."""
    p = claude_path()
    if p:
        return p
    emit = on_event or (lambda name, payload: None)
    emit("repair", {"phase": "bootstrap", "message": "Installing Claude Code (one-time)…"})
    try:
        (installer or _default_installer)()
    except Exception as e:  # noqa: BLE001
        emit("repair", {"phase": "error", "message": f"Claude Code install failed: {e}"})
        return None
    return claude_path()


# ─── invocation construction ────────────────────────────────────────────────
def build_repair_prompt(step_id: str, title: str, command: str,
                        error: str, probe: dict) -> str:
    return (
        f"An ABA install step failed on this Mac. Diagnose the SYSTEM cause, fix "
        f"it within the user's space, then verify.\n\n"
        f"Failed step: {step_id} — {title}\n"
        f"Command:\n{command}\n\n"
        f"Error / last output:\n{error}\n\n"
        f"System probe:\n{json.dumps(probe, indent=2)}\n\n"
        f"Apply the smallest fix that lets the command succeed (see your "
        f"instructions for common macOS failure modes). Do NOT re-run the whole "
        f"install. When done, briefly state what you changed."
    )


def build_claude_argv(claude: str, *, prompt: str,
                      recipe_path: Path = RECIPE_PATH,
                      allowed_tools: Optional[list[str]] = None,
                      add_dir: Optional[str] = None,
                      stream: bool = False) -> list[str]:
    # stream-json (+ --verbose) surfaces each assistant message / tool_use as it
    # happens so we can show the agent's actions live in the "Show details" log;
    # plain json returns only the final summary.
    output = (["--output-format", "stream-json", "--verbose"] if stream
              else ["--output-format", "json"])
    argv = [
        claude, "-p", prompt,
        "--append-system-prompt-file", str(recipe_path),
        "--permission-mode", "dontAsk",
        "--allowedTools", ",".join(allowed_tools or DEFAULT_ALLOWED_TOOLS),
    ] + output
    if add_dir:
        argv += ["--add-dir", add_dir]
    return argv


# ─── stream-json parsing (#2) ───────────────────────────────────────────────
def _summarize_stream_event(evt: dict) -> Optional[str]:
    """Turn one `--output-format stream-json` event into a short human line for
    the details log, or None to ignore it. Surfaces what the agent SAYS and
    DOES (text + each tool_use); skips verbose tool results and bookkeeping."""
    t = evt.get("type")
    if t != "assistant":
        return None
    blocks = (evt.get("message") or {}).get("content") or []
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            txt = (b.get("text") or "").strip()
            if txt:
                out.append(txt)
        elif b.get("type") == "tool_use":
            name = b.get("name", "?")
            inp = b.get("input") or {}
            detail = inp.get("command") or inp.get("file_path") or \
                ", ".join(f"{k}={v}" for k, v in list(inp.items())[:2])
            out.append(f"🔧 {name}: {str(detail)[:120]}")
    return "  ·  ".join(out) if out else None


def _consume_stream(lines, emit) -> dict:
    """Parse an NDJSON line stream, emitting per-event repair messages; return
    the final {returncode, result, is_error} from the terminal `result` event."""
    final: dict = {"returncode": 0}
    for raw in lines:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            evt = json.loads(raw)
        except Exception:  # noqa: BLE001
            continue
        if evt.get("type") == "result":
            final["result"] = evt.get("result") or ""
            final["is_error"] = bool(evt.get("is_error"))
            continue
        msg = _summarize_stream_event(evt)
        if msg:
            emit("repair", {"phase": "step", "message": msg})
    return final


def _streaming_runner(argv: list[str], *, cwd: Optional[str], env: dict,
                      on_event: Callable) -> dict:
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    final = _consume_stream(proc.stdout, on_event)
    try:
        proc.wait(timeout=600)
    except Exception:  # noqa: BLE001
        proc.kill()
    final.setdefault("returncode", proc.returncode if proc.returncode is not None else 1)
    if proc.returncode and not final.get("result"):
        final["result"] = (proc.stderr.read() if proc.stderr else "")[-400:]
    return final


def _default_runner(argv: list[str], *, cwd: Optional[str], env: dict) -> dict:
    """Run `claude -p …` and parse its --output-format json result."""
    proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=600)
    out = (proc.stdout or "").strip()
    parsed = {}
    if out:
        try:
            parsed = json.loads(out)
        except Exception:  # noqa: BLE001
            parsed = {"result": out}
    parsed.setdefault("returncode", proc.returncode)
    if proc.returncode != 0 and not parsed.get("result"):
        parsed["result"] = (proc.stderr or "")[-400:]
    return parsed


# ─── orchestration ──────────────────────────────────────────────────────────
def _run_agent(prompt: str, *, cwd, claude, runner, on_event, stream,
               allowed_tools, start_msg: str, skip_msg: str) -> RepairOutcome:
    """Shared core: run `claude -p` (streaming or not), emit events, return the
    outcome. `runner` injectable for tests; streaming used only for real runs."""
    emit = on_event or (lambda name, payload: None)
    claude = claude or claude_path()
    if not claude:
        emit("repair", {"phase": "skip", "message": skip_msg})
        return RepairOutcome(attempted=False, ok=False, reason="claude not available")
    # No ABA credential → invoking claude is doomed (it'd return "Not logged in").
    # Skip with attempted=False so the executor halts cleanly instead of retrying.
    creds = _aba_credential_env()
    if not creds:
        emit("repair", {"phase": "skip", "message": _NO_CREDENTIAL_MSG})
        return RepairOutcome(attempted=False, ok=False, reason="no aba credential")
    argv = build_claude_argv(claude, prompt=prompt, allowed_tools=allowed_tools,
                             add_dir=cwd, stream=stream)
    emit("repair", {"phase": "start", "message": start_msg})
    env = dict(os.environ)
    env.setdefault("DISABLE_AUTOUPDATER", "1")   # don't self-update mid-install
    env.update(creds)
    if runner is None and stream:
        def run(a, *, cwd, env):
            return _streaming_runner(a, cwd=cwd, env=env, on_event=emit)
    else:
        run = runner or _default_runner
    try:
        parsed = run(argv, cwd=cwd, env=env)
    except Exception as e:  # noqa: BLE001
        emit("repair", {"phase": "error", "message": str(e)})
        return RepairOutcome(attempted=True, ok=False, reason=f"runner failed: {e}")
    diagnosis = (parsed.get("result") or "").strip()
    # Stale or rejected credential — claude ran but couldn't talk to the API.
    # Treat as no-attempt so the executor doesn't retry; the credential isn't
    # going to fix itself between attempts.
    if _looks_like_auth_failure(diagnosis):
        emit("repair", {"phase": "skip", "message": _AUTH_FAILED_MSG})
        return RepairOutcome(attempted=False, ok=False,
                             reason="claude auth failed", diagnosis=diagnosis,
                             raw=parsed)
    ok = (parsed.get("returncode", 0) == 0) and not parsed.get("is_error")
    emit("repair", {"phase": "done", "ok": ok,
                    "message": diagnosis[:400] or ("done" if ok else "could not complete")})
    return RepairOutcome(attempted=True, ok=ok, diagnosis=diagnosis, raw=parsed)


def run_repair(step_id: str, title: str, command: str, error: str, *,
               cwd: Optional[str] = None, claude: Optional[str] = None,
               runner: Optional[Callable] = None,
               allowed_tools: Optional[list[str]] = None,
               on_event: Optional[Callable[[str, dict], None]] = None,
               stream: bool = True) -> RepairOutcome:
    """Drive one repair round for a failed step. `runner` injectable for tests."""
    prompt = build_repair_prompt(step_id, title, command, error, probe_system())
    return _run_agent(prompt, cwd=cwd, claude=claude, runner=runner,
                      on_event=on_event, stream=stream, allowed_tools=allowed_tools,
                      start_msg=f"Asking Claude Code to diagnose “{title}”…",
                      skip_msg="Claude Code not available — cannot auto-repair.")


# ─── adaptive pre-flight (#3) ───────────────────────────────────────────────
def build_preflight_prompt(plan_summary: str, probe: dict) -> str:
    return (
        f"PRE-FLIGHT check before an ABA install runs on this Mac. The install "
        f"will perform these steps:\n{plan_summary}\n\n"
        f"System probe:\n{json.dumps(probe, indent=2)}\n\n"
        f"Detect conditions that would make those steps fail (Gatekeeper "
        f"quarantine on downloaded binaries, missing tools, proxy/network, low "
        f"disk) and PROACTIVELY fix only what you safely can within the user's "
        f"space. Do NOT run the install itself. Briefly report what you found "
        f"and any fixes applied."
    )


def run_preflight(plan_summary: str, *, cwd: Optional[str] = None,
                  claude: Optional[str] = None, runner: Optional[Callable] = None,
                  allowed_tools: Optional[list[str]] = None,
                  on_event: Optional[Callable[[str, dict], None]] = None,
                  stream: bool = True) -> RepairOutcome:
    """Proactive pre-flight: probe + pre-fix known blockers before the install."""
    prompt = build_preflight_prompt(plan_summary, probe_system())
    return _run_agent(prompt, cwd=cwd, claude=claude, runner=runner,
                      on_event=on_event, stream=stream, allowed_tools=allowed_tools,
                      start_msg="Pre-flight: checking this Mac for install blockers…",
                      skip_msg="Claude Code not available — skipping pre-flight.")


def make_repair_hook(*, cwd: Optional[str] = None,
                     runner: Optional[Callable] = None,
                     on_event: Optional[Callable[[str, dict], None]] = None,
                     enabled: bool = True,
                     ensure: bool = False) -> Callable:
    """Returns an Executor `on_step_failed(step, result, attempt) -> bool` hook.
    Runs a repair round and tells the executor to retry the step iff a repair was
    attempted (the step's actual re-run is the real verifier; the executor caps
    the number of attempts).

    `ensure=True` bootstraps the `claude` binary (ensure_claude) on the FIRST
    failure — so the happy path never pays for it, only a real failure does."""
    state = {"resolved": not ensure, "claude": None}
    emit = on_event or (lambda name, payload: None)

    def hook(step, result, attempt: int) -> bool:
        if not enabled:
            return False
        # Don't even bootstrap claude if we'd have no credential to feed it —
        # the install would just download a binary we can't use.
        if not _aba_credential_env():
            emit("repair", {"phase": "skip", "message": _NO_CREDENTIAL_MSG})
            return False
        if not state["resolved"]:
            state["resolved"] = True
            state["claude"] = ensure_claude(on_event=on_event)
        err = (result.error or "")
        cmd = step.commands[-1] if getattr(step, "commands", None) else ""
        outcome = run_repair(step.id, step.title, cmd, err, cwd=cwd,
                             claude=state["claude"], runner=runner, on_event=on_event)
        return outcome.attempted
    return hook
