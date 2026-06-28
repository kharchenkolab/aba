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
from core.runtime.history_prep import api_messages                  # noqa: E402
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

    print("api_messages STRIPS the UI-only attachments block before the model")
    hist = [{"role": "user", "thread_id": None, "focus_entity_id": None, "ts": 1,
             "content": [{"type": "text", "text": "look at this"},
                         {"type": "attachments", "items": [item]}]}]
    out = api_messages(hist)
    check("attachments block removed; text kept",
          out == [{"role": "user", "content": [{"type": "text", "text": "look at this"}]}], str(out))

    print("build_injection: ephemeral context note + vision only for images")
    note, imgs = build_injection([ref])
    check("note names the file + its absolute path", "report.csv" in note and ref["path"] in note, note[:120])
    check("non-image → no vision block", imgs == [])

    try:
        from PIL import Image
        png = attachments_root(PID, "thr_1") / "shot.png"
        Image.new("RGB", (8, 8), (255, 0, 0)).save(png)
        iref = {"name": "shot.png", "path": str(png), "kind": "image",
                "is_image": True, "size_bytes": png.stat().st_size, "url": "/x"}
        note_i, imgs_i = build_injection([iref], allow_vision=True)
        check("image → 1 Anthropic vision block",
              len(imgs_i) == 1 and imgs_i[0].get("type") == "image"
              and imgs_i[0].get("source", {}).get("type") == "base64", str(imgs_i)[:120])
        check("image note flags it as shown in chat", "shown above" in note_i)
        _, imgs_off = build_injection([iref], allow_vision=False)
        check("allow_vision=False → no vision block even for an image", imgs_off == [])
    except ImportError:
        print("  [skip] PIL unavailable — vision-block assertions skipped")

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL CHAT-ATTACHMENTS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
