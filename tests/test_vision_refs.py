"""Vision refs: history stores the REFERENCE, egress materializes recent-K.

The source fix for the oversized-history class (the ~1.3MB base64 row): the
payload never enters durable history — a small image_ref block does — and
guide inflates the most recent K refs into real vision blocks at prompt
assembly, upstream of the prep hash and every runtime. Guards:
  - packing swaps image → image_ref and never stores base64;
  - legacy producers (no ref) keep the inline path (Tier-1 covers them);
  - materialization: recent-K → real image blocks (deterministic bytes),
    older/deleted → honest re-view stubs; NO image_ref ever survives to
    the outgoing list (an unknown block type at the API is a 400);
  - entity-id refs resolve through the entity's artifact_path.
"""
import base64
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
_tmp = tempfile.mkdtemp(prefix="aba_vref_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_tmp, "t.db"))

from content.bio import vision_refs as vr  # noqa: E402

pytestmark = pytest.mark.bio

# 1x1 red PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAF"
    "AAH/q842iQAAAABJRU5ErkJggg==")


def _envelope(path="/x/a.png"):
    return {"path": path, "kind": "image",
            "_vision_blocks": [
                {"type": "text", "text": "Image a.png:"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": "AAAA"}}],
            "_vision_ref": {"tool": "view_file", "path": path}}


def test_pack_swaps_image_for_ref_and_never_stores_base64(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_PNG)
    out = vr.pack_tool_result_content(_envelope(path=str(p)))
    assert out[0]["type"] == "text"
    assert out[1]["type"] == "image_ref" and out[1]["path"] == str(p)
    assert "AAAA" not in str(out), "payload leaked into durable history"


def test_pack_legacy_envelope_keeps_inline_blocks():
    env = _envelope()
    del env["_vision_ref"]
    out = vr.pack_tool_result_content(env)
    assert out[1]["type"] == "image"          # legacy inline — Tier-1's job


def test_pack_non_vision_envelope_returns_none():
    assert vr.pack_tool_result_content({"status": "ok"}) is None


def _hist_with_refs(tmp_path, n=6):
    msgs = []
    for i in range(n):
        p = tmp_path / f"im{i}.png"
        p.write_bytes(_PNG)
        msgs.append({"role": "assistant", "content": [
            {"type": "tool_use", "id": f"v{i}", "name": "view_file",
             "input": {"path": str(p)}}]})
        msgs.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"v{i}", "content": [
                {"type": "text", "text": f"Image im{i}.png:"},
                {"type": "image_ref", "tool": "view_file", "path": str(p),
                 "media_type": "image/png"}]}]})
    return msgs


def test_materialize_recent_k_and_stub_older(tmp_path):
    msgs = _hist_with_refs(tmp_path, 6)
    out = vr.materialize_image_refs(msgs, k=4)
    flat = [b for m in out for tr in m["content"] if isinstance(tr, dict)
            and isinstance(tr.get("content"), list) for b in tr["content"]]
    images = [b for b in flat if b.get("type") == "image"]
    stubs = [b for b in flat if b.get("type") == "text"
             and "re-view via view_file" in b.get("text", "")]
    assert len(images) == 4 and len(stubs) == 2
    assert images[0]["source"]["data"], "materialized block carries real bytes"
    # NOTHING un-materialized may reach the API (unknown type = 400)
    assert not [b for b in flat if b.get("type") == "image_ref"]
    # deterministic: same bytes both calls → prefix-stable across generations
    assert vr.materialize_image_refs(msgs, k=4) == out


def test_materialize_deleted_file_degrades_to_stub(tmp_path):
    msgs = _hist_with_refs(tmp_path, 2)
    os.unlink(tmp_path / "im1.png")           # the RECENT one vanishes
    out = vr.materialize_image_refs(msgs, k=4)
    flat = [b for m in out for tr in m["content"] if isinstance(tr, dict)
            and isinstance(tr.get("content"), list) for b in tr["content"]]
    assert not [b for b in flat if b.get("type") == "image_ref"]
    assert any("re-view via" in b.get("text", "") for b in flat
               if b.get("type") == "text")


def test_materialize_no_refs_is_fast_path_identity():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert vr.materialize_image_refs(msgs, k=4) is msgs


def test_entity_ref_resolves_via_artifact_path(tmp_path, monkeypatch):
    p = tmp_path / "fig.png"
    p.write_bytes(_PNG)
    monkeypatch.setattr("core.graph.entities.get_entity",
                        lambda eid: {"id": eid, "artifact_path": str(p)})
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "image_ref", "tool": "view_artifact",
             "entity_id": "ent_1", "media_type": "image/png"}]}]}]
    out = vr.materialize_image_refs(msgs, k=4)
    b = out[0]["content"][0]["content"][0]
    assert b["type"] == "image" and b["source"]["data"]


def test_path_based_view_round_trips_to_a_real_image(tmp_path):
    """`view_artifact(path=…)` carries NO entity_id — the common shape, since an
    intermediate a run just wrote has no entity. Its ref must still resolve, or
    egress hands the model a text stub instead of the image it asked to look at,
    and the tool silently stops working (the storage win would be paid for with
    a correctness regression).

    Fails on an entity-only ref: materialization yields ['text','text'].
    """
    import json
    from content.bio.mcp_servers.aba_core.tools import entity_ops as EO
    from content.bio import vision_refs as VR

    png = tmp_path / "fig.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"Z" * 200_000)
    env = EO._vision_envelope(None, None, "", str(png), {}, "fig.png",
                              png.read_bytes(), "image/png")
    packed = VR.pack_tool_result_content(env)

    # (a) durable history is small — the payload never lands in the DB
    assert len(json.dumps(packed)) < 2_000, "vision payload leaked into history"

    # (b) …and the recent-K egress puts a REAL image back in front of the model
    hist = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": packed}]}]
    out = VR.materialize_image_refs(hist)
    kinds = [b.get("type") for b in out[0]["content"][0]["content"]]
    assert "image" in kinds, (
        f"path-based ref did not materialize (got {kinds}) — the model sees no "
        f"image, so view_artifact is functionally dead on this shape")
# Appended to tests/test_vision_refs.py post-sweep. Armed sequence: append,
# run → BOTH must FAIL on 639091e4 (PROVEN); apply the fix script; green.
# (The entity-less shape itself is already guarded by their 639091e4 test —
# not duplicated here.)


def test_pdf_rasterization_mints_no_ref(tmp_path):
    """Resolvable is not reproducible: a rasterized page's ref would pass
    is_file() on the .pdf yet the egress materializer cannot decode it — the
    model would get a stub for a page it just asked to see. The PDF branch
    must keep inline blocks (Tier-1 ages them out)."""
    from content.bio.mcp_servers.aba_core.tools import entity_ops as eo
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    env = eo._vision_envelope("ent_1", "figure", "t", str(pdf), {},
                              "d.pdf (page 1/2)", _PNG, "image/png",
                              materializable=False)
    assert "_vision_ref" not in env
    content = vr.pack_tool_result_content(env)
    assert any(b.get("type") == "image" for b in content), \
        "PDF page lost its inline image"
    # and the degenerate end-to-end: even if a pdf-path ref EXISTED in old
    # history, egress must degrade to a stub, never an image_ref leak
    msgs = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": [
            {"type": "image_ref", "tool": "view_artifact",
             "path": str(pdf), "media_type": "image/png"}]}]}]
    out = vr.materialize_image_refs(msgs, k=4)
    flat = out[0]["content"][0]["content"]
    assert not any(b.get("type") == "image_ref" for b in flat)


def test_pack_backstop_refuses_unresolvable_ref(tmp_path):
    """Correctness over cost: a ref that resolves to nothing at MINT time is
    not swapped in — inline blocks are kept."""
    env = _envelope(path=str(tmp_path / "never-existed.png"))
    content = vr.pack_tool_result_content(env)
    assert any(b.get("type") == "image" for b in content)
    assert not any(b.get("type") == "image_ref" for b in content)
