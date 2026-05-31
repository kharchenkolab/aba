"""One-shot probe: will a Claude Code *subscription* OAuth bearer token work for a
RAW Anthropic Messages API call (claude-haiku-4-5)?

If yes, the prompt-regression harness can authenticate with that token (auth_token=)
instead of the project .env api-key -> spend moves to the subscription Agent-SDK
credit pool, off the .env key, with ZERO change to the (faithful) request itself.

Token sources, in priority order:
  1. $CLAUDE_CODE_OAUTH_TOKEN          (the long-lived `claude setup-token` artifact)
  2. a token-file path passed as argv[1]
  3. ~/.claude/.credentials.json       (the stored Claude Code OAuth access token)
The token is NEVER printed. Fires ONE ~16-token Haiku call (fractions of a cent).
"""
import os, sys, json, time
import anthropic

CRED = os.path.expanduser("~/.claude/.credentials.json")


def _token():
    t = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if t:
        return t.strip(), "env CLAUDE_CODE_OAUTH_TOKEN", None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return open(sys.argv[1]).read().strip(), f"file {sys.argv[1]}", None
    if os.path.exists(CRED):
        d = json.load(open(CRED))
        oa = d.get("claudeAiOauth") or d.get("oauth") or {}
        tok = oa.get("accessToken") or oa.get("access_token")
        if tok:
            exp = oa.get("expiresAt") or oa.get("expires_at")
            return tok.strip(), "~/.claude/.credentials.json (claudeAiOauth.accessToken)", exp
    sys.exit("no token found (set CLAUDE_CODE_OAUTH_TOKEN, pass a file, or log in via Claude Code)")


def _attempt(label, tok, extra_headers=None):
    client = anthropic.Anthropic(auth_token=tok)
    try:
        r = client.messages.create(
            model="claude-haiku-4-5-20251001", max_tokens=16,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            extra_headers=extra_headers or {})
        txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        print(f"  [{label:10}] ACCEPTED (200)  reply={txt!r}  in={r.usage.input_tokens} out={r.usage.output_tokens}")
        return True
    except anthropic.APIStatusError as e:
        print(f"  [{label:10}] REJECTED ({e.status_code})  {str(e)[:240]}")
    except Exception as e:  # noqa: BLE001
        print(f"  [{label:10}] ERROR {type(e).__name__}: {str(e)[:240]}")
    return False


def main():
    tok, src, exp = _token()
    note = ""
    if exp:
        try:
            secs = (float(exp) / 1000.0) - time.time() if float(exp) > 1e11 else float(exp) - time.time()
            note = f", expires in ~{secs/3600:.1f}h" + ("  (EXPIRED!)" if secs < 0 else "")
        except Exception:  # noqa: BLE001
            note = f", expiresAt={exp}"
    print(f"token source: {src}  ({len(tok)} chars, value hidden{note})")
    print("probing raw messages.create with Bearer auth_token (claude-haiku-4-5):")
    ok = _attempt("plain", tok)
    if not ok:
        # subscription OAuth on the public API has historically needed a beta opt-in header
        ok = _attempt("oauth-beta", tok, {"anthropic-beta": "oauth-2025-04-20"})
    print("\nRESULT:", "OAuth bearer WORKS for raw Messages API -> budget shift viable"
          if ok else "OAuth bearer NOT accepted for raw calls -> stay on .env key")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
