"""Chat attachments (composer paperclip / clipboard paste).

Backend contract:
  - save_attachment stashes the file in a per-thread SCRATCH dir (no dataset
    entity — the agent registers it only if asked);
  - the persisted user message carries a UI-only `attachments` block that
    api_messages STRIPS before the model;
  - build_injection produces the ephemeral agent context (note + vision blocks
    for images) for the arriving turn only.

Run:  .venv/bin/python tests/test_chat_attachments.py
"""
from __future__ import annotations
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_attach_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "a.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                              # noqa: E402
from core.runtime.history_prep import api_messages, strip_ui_blocks  # noqa: E402
from core.runtime.attachments import save_attachment, ui_item, build_injection, attachments_root  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    init_db()
    PID = "prj_test"

    print("save_attachment → scratch file, no dataset entity, correct ref")
    ref = save_attachment(PID, "thr_1", "report.csv", io.BytesIO(b"a,b\n1,2\n"))
    check("ref facets (name/kind/is_image/size)",
          ref["name"] == "report.csv" and ref["kind"] == "csv"
          and ref["is_image"] is False and ref["size_bytes"] == 8, str(ref))
    check("stored under per-thread .attachments scratch (not a dataset dir)",
          ".attachments" + os.sep + "thr_1" + os.sep in ref["path"] and Path(ref["path"]).is_file(),
          ref["path"])
    check("bytes written verbatim", Path(ref["path"]).read_bytes() == b"a,b\n1,2\n")
    check("serve url is project-pinned", ref["url"] == "/api/attachments/thr_1/report.csv?project_id=prj_test",
          ref["url"])
    # name collision → unique path, not overwrite
    ref2 = save_attachment(PID, "thr_1", "report.csv", io.BytesIO(b"x"))
    check("name collision deduped (no overwrite)", ref2["path"] != ref["path"]
          and Path(ref["path"]).read_bytes() == b"a,b\n1,2\n")

    print("ui_item is chip-only (no disk path leaked to the persisted block)")
    item = ui_item({**ref, "path": "/secret/report.csv"})
    check("ui_item drops the disk path", "path" not in item and item["name"] == "report.csv"
          and item["url"] == ref["url"])

    print("strip_ui_blocks drops the attachments block at the real API boundary (core/llm.py)")
    content = [{"type": "text", "text": "look at this"}, {"type": "attachments", "items": [item]}]
    check("strip_ui_blocks removes attachments, keeps text + real blocks",
          strip_ui_blocks(content) == [{"type": "text", "text": "look at this"}], str(strip_ui_blocks(content)))
    check("strip_ui_blocks passes a string content through untouched",
          strip_ui_blocks("hello") == "hello")
    check("strip_ui_blocks keeps image/tool blocks",
          strip_ui_blocks([{"type": "image"}, {"type": "tool_use"}]) == [{"type": "image"}, {"type": "tool_use"}])

    print("api_messages (boundary shaper) also strips it")
    hist = [{"role": "user", "thread_id": None, "focus_entity_id": None, "ts": 1, "content": content}]
    out = api_messages(hist)
    check("attachments block removed; text kept",
          out == [{"role": "user", "content": [{"type": "text", "text": "look at this"}]}], str(out))

    print("build_injection is NOTICE-ONLY — files do NOT auto-enter the model context")
    note = build_injection([ref])
    check("notice is a plain string naming the file + its path",
          isinstance(note, str) and "report.csv" in note and ref["path"] in note, str(note)[:120])
    check("no attachments → no notice", build_injection([]) is None)
    img_ref = {"name": "shot.png", "path": "/x/shot.png", "kind": "image",
               "is_image": True, "size_bytes": 9, "url": "/x"}
    note_img = build_injection([img_ref])
    check("an IMAGE attachment is STILL notice-only (no vision auto-inject)",
          isinstance(note_img, str) and "shot.png" in note_img and "image" not in str(type(note_img)).lower()
          and "base64" not in note_img)

    print("API-VALIDITY GUARD: api_messages output is ENTIRELY Anthropic-allowed blocks")
    from core.runtime.history_prep import ALLOWED_API_BLOCK_TYPES
    history = [  # every block type we persist — INCLUDING the UI-only attachments block (the live 400)
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "attachments", "items": [item]},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
    ]
    sent = {b["type"] for m in api_messages(history)
            for b in (m["content"] if isinstance(m["content"], list) else []) if isinstance(b, dict)}
    check("NO attachments block survives to the API (the regression that would've caught the bug)",
          "attachments" not in sent, str(sent))
    check("EVERY block sent is an Anthropic-allowed type", not (sent - ALLOWED_API_BLOCK_TYPES),
          f"disallowed leaked: {sent - ALLOWED_API_BLOCK_TYPES}")
    check("legit blocks preserved", {"text", "image", "tool_use", "tool_result"} <= sent, str(sent))

    print("view_file: the agent's EXPLICIT pull-into-context (image→vision, text, unknown)")
    from content.bio.tools.view_file import view_file_tool
    vf = Path(_tmp) / "vf"; vf.mkdir(parents=True, exist_ok=True)
    (vf / "t.csv").write_text("a,b\n1,2\n")
    rt = view_file_tool({"path": str(vf / "t.csv")})
    check("text → kind=text + content", rt.get("kind") == "text" and "a,b" in (rt.get("text") or ""), str(rt)[:100])
    (vf / "x.bin").write_bytes(b"\x1f\x8b\x08\x00\x00rest-of-some-binary")   # gzip magic
    rb = view_file_tool({"path": str(vf / "x.bin")})
    check("unknown/binary → kind=binary + magic type_guess + head",
          rb.get("kind") == "binary" and "gzip" in (rb.get("type_guess") or "") and rb.get("head_hex"),
          str(rb)[:120])
    check("missing file → error (path guard)", "error" in view_file_tool({"path": str(vf / "nope.csv")}))
    try:
        from PIL import Image
        Image.new("RGB", (8, 8), (0, 255, 0)).save(vf / "s.png")
        ri = view_file_tool({"path": str(vf / "s.png")})
        vb = ri.get("_vision_blocks") or []
        check("image → _vision_blocks [text, image] (the model SEES it via the envelope)",
              ri.get("kind") == "image" and len(vb) == 2
              and vb[0].get("type") == "text" and vb[1].get("type") == "image"
              and vb[1].get("source", {}).get("type") == "base64", str(ri)[:120])
    except ImportError:
        print("  [skip] PIL unavailable — image view assertion skipped")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL CHAT-ATTACHMENTS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
