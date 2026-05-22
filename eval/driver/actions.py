"""Action vocabulary for the simulated scientist (eval Stage 3, Layer A).

Each action mirrors a UI affordance and is exposed two ways:
  - TOOLS: Anthropic tool schemas the LLM scientist chooses from;
  - execute(name, input, ctx): runs it against the real backend API.

Start minimal; grow as scenarios demand. `ctx` is a SimpleNamespace carrying
api base, the current focus id, the last search results, and a done flag.
"""
from __future__ import annotations
import json
import urllib.parse
import urllib.request

TOOLS = [
    {
        "name": "send_message",
        "description": "Talk to Guide, the analysis agent. It can load data, run "
                       "Python, make plots and tables, and explain results. Your "
                       "message is scoped to whatever you're currently focused on.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "What to ask Guide to do or explain."}},
            "required": ["text"]},
    },
    {
        "name": "focus",
        "description": "Focus an artifact by id (e.g. a dataset or figure). This is "
                       "what you're looking at; Guide's next reply uses its context. "
                       "Use 'workspace' for the whole project.",
        "input_schema": {"type": "object", "properties": {
            "entity_id": {"type": "string"}}, "required": ["entity_id"]},
    },
    {
        "name": "search",
        "description": "Search artifacts and the conversation by keyword (use this to "
                       "find things not shown in the tree).",
        "input_schema": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "pin",
        "description": "Pin a figure or table so it's kept in the project.",
        "input_schema": {"type": "object", "properties": {
            "entity_id": {"type": "string"}}, "required": ["entity_id"]},
    },
    {
        "name": "promote_figure",
        "description": "Promote a figure to a Result, recording your interpretation "
                       "of what it shows.",
        "input_schema": {"type": "object", "properties": {
            "entity_id": {"type": "string"},
            "interpretation": {"type": "string"}}, "required": ["entity_id", "interpretation"]},
    },
    {
        "name": "save_finding",
        "description": "Record a Finding from evidence (results/figures), with a short "
                       "summary and optional caveats.",
        "input_schema": {"type": "object", "properties": {
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "maturity": {"type": "string", "enum": ["draft", "candidate", "checked"]}},
            "required": ["title", "summary"]},
    },
    {
        "name": "done",
        "description": "Stop: you've achieved the goal, or cannot make further progress. "
                       "Summarize what you concluded.",
        "input_schema": {"type": "object", "properties": {
            "summary": {"type": "string"}}, "required": ["summary"]},
    },
]


def _get(api, path):
    with urllib.request.urlopen(f"{api}{path}", timeout=30) as r:
        return json.loads(r.read())


def _send(api, path, body, method="POST"):
    req = urllib.request.Request(
        f"{api}{path}", data=json.dumps(body).encode(), method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _consume_chat(api, text, focus_id) -> dict:
    """POST /api/chat and drain the SSE stream. Returns the assistant text, the
    tools Guide ran, and how many new entities registered."""
    body = json.dumps({"text": text, "focus_entity_id": focus_id}).encode()
    req = urllib.request.Request(f"{api}/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    out, tools, n_entities, err = [], [], 0, None
    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
    with urllib.request.urlopen(req, timeout=600) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            if t == "delta":
                out.append(ev.get("text", ""))
            elif t == "tool_start":
                tools.append(ev.get("name"))
            elif t == "entity_registered":
                n_entities += 1
            elif t == "usage":
                usage = {"input": ev.get("input", 0), "output": ev.get("output", 0),
                         "cache_read": ev.get("cache_read", 0), "cache_write": ev.get("cache_write", 0)}
            elif t == "error":
                err = ev.get("text", "error")
            elif t == "done":
                break
    return {"text": "".join(out).strip(), "tools": tools,
            "n_entities": n_entities, "error": err, "usage": usage}


def execute(name: str, inp: dict, ctx) -> dict:
    api = ctx.api
    if name == "send_message":
        r = _consume_chat(api, inp["text"], ctx.focus_id)
        if r["error"]:
            return {"observation": f"Guide error: {r['error']}"}
        extra = ""
        if r["tools"]:
            extra += f" (ran: {', '.join(r['tools'])})"
        if r["n_entities"]:
            extra += f" [{r['n_entities']} new artifact(s) appeared in the tree]"
        return {"observation": f"Guide: {r['text'][:1200]}{extra}",
                "guide_usage": r["usage"]}

    if name == "focus":
        eid = inp["entity_id"]
        if eid != "workspace" and not any(e["id"] == eid for e in _get(api, "/entities")):
            return {"observation": f"No artifact with id {eid}."}
        ctx.focus_id = eid
        return {"observation": f"Focused {eid}.", "focus_id": eid}

    if name == "search":
        res = _get(api, f"/search?q={urllib.parse.quote(inp['query'])}")
        ctx.last_search = res
        n = len(res.get("entities", [])) + len(res.get("messages", []))
        return {"observation": f"Search '{inp['query']}': {n} hit(s) (see SEARCH RESULTS).",
                "search": res}

    if name == "pin":
        _send(api, f"/entities/{inp['entity_id']}", {"pinned": True}, method="PATCH")
        return {"observation": f"Pinned {inp['entity_id']}."}

    if name == "promote_figure":
        ent = _send(api, f"/entities/{inp['entity_id']}/promote-to-result",
                    {"interpretation": inp["interpretation"]})
        return {"observation": f"Promoted to result {ent['id']}: {ent['title']}.",
                "created": ent["id"]}

    if name == "save_finding":
        ent = _send(api, "/findings/from-draft", {
            "title": inp["title"], "summary": inp.get("summary", ""),
            "evidence_ids": inp.get("evidence_ids", []),
            "caveats": [], "status": inp.get("maturity", "candidate")})
        return {"observation": f"Saved finding {ent['id']}: {ent['title']}.",
                "created": ent["id"]}

    if name == "done":
        ctx.done = True
        return {"observation": f"Done: {inp.get('summary', '')}"}

    return {"observation": f"Unknown action {name}."}
