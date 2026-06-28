"""Chat attachments — the paperclip in the Guide composer.

A *lighter-than-a-dataset* upload: the file is stashed in a per-thread scratch
area the agent can read (it registers it as a dataset only if the user asks —
the user's design call). The chat turn carries a ref; guide.py persists a
UI-only ``attachments`` content block (rendered as chips / image thumbnails) and,
for THIS turn only, ephemerally injects a context note (+ for images, a vision
block) so the agent is well-contextualized to follow up — mirroring the
annotation_image path. The scratch dir lives under the project data dir, so the
agent's read_file / inspect_upload reach it by absolute path.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from core.config import project_data_dir
from core.data.paths import unique_path

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _image_vision_block(path: str) -> Optional[dict]:
    """A self-contained image → Anthropic vision block, downscaled. Core-pure
    (core/ must not depend on content/, so this doesn't reuse the bio
    promote._image_block) — same shape, JPEG-downscaled to a 1568px long edge."""
    import base64
    p = Path(path) if path else None
    if p is None or not p.is_file():
        return None
    if p.suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    try:
        import io as _io
        from PIL import Image
        with Image.open(p) as im:
            if max(im.size) > 1568:
                im.thumbnail((1568, 1568))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            buf = _io.BytesIO()
            im.save(buf, format="JPEG", quality=85)
            data = base64.b64encode(buf.getvalue()).decode()
        return {"type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}
    except Exception:  # noqa: BLE001 — PIL missing / decode failure: skip vision, keep the note
        try:
            raw = p.read_bytes()
            if len(raw) > 5 * 1024 * 1024:
                return None
            media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "webp": "image/webp", "gif": "image/gif"}[p.suffix.lower().lstrip(".")]
            return {"type": "image", "source": {"type": "base64", "media_type": media,
                                                "data": base64.b64encode(raw).decode()}}
        except Exception:  # noqa: BLE001
            return None


def attachments_root(pid: str, thread_id: str) -> Path:
    """The per-thread scratch dir for chat attachments (created on demand)."""
    d = project_data_dir(pid) / ".attachments" / (thread_id or "default")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _kind(dest: Path, is_image: bool) -> str:
    return "image" if is_image else (dest.suffix.lower().lstrip(".") or "file")


def save_attachment(pid: str, thread_id: str, filename: str, src) -> dict:
    """Stash an uploaded/pasted file in the thread's scratch area. Returns the
    ref the chat turn carries (also the shape persisted in the UI block)."""
    thread_id = thread_id or "default"
    dest = unique_path(attachments_root(pid, thread_id) / Path(filename or "upload").name)
    with dest.open("wb") as f:
        shutil.copyfileobj(src, f)
    is_image = dest.suffix.lower() in _IMAGE_SUFFIXES
    return {
        "name": dest.name,
        "path": str(dest),
        "kind": _kind(dest, is_image),
        "is_image": is_image,
        "size_bytes": dest.stat().st_size,
        "thread_id": thread_id,
        # served by GET /api/attachments/{thread_id}/{name}; project pins via query
        "url": f"/api/attachments/{thread_id}/{dest.name}?project_id={pid}",
    }


def ui_item(a: dict) -> dict:
    """The lightweight per-attachment shape persisted in the UI block (no disk
    path — the chip/thumbnail only needs name/kind/url)."""
    return {k: a.get(k) for k in ("name", "kind", "is_image", "size_bytes", "url")}


def build_injection(attachments: Optional[list[dict]],
                    *, allow_vision: bool = True) -> tuple[Optional[str], list[dict]]:
    """Build the EPHEMERAL agent context for the arriving turn: a context note
    (text) + vision blocks for images. Not persisted — rehydrated from the param
    only on the turn the attachment arrives, exactly like annotation_image.

    Returns (note_text, image_blocks). image_blocks is empty when allow_vision is
    False (e.g. fake/no-vision mode)."""
    if not attachments:
        return None, []
    lines = ["The user attached the following file(s) with this message. They are "
             "scratch uploads in the project data dir — inspect them with "
             "inspect_upload / read_file at the paths below, and register one as a "
             "dataset only if the user asks. Images are shown in the chat:"]
    image_blocks: list[dict] = []
    for a in attachments:
        path, name = a.get("path"), a.get("name")
        size = a.get("size_bytes")
        lines.append(f"  - {name} ({a.get('kind')}, {size} bytes) at {path}"
                     + (" [image — shown above]" if a.get("is_image") else ""))
        if allow_vision and a.get("is_image") and path:
            blk = _image_vision_block(path)
            if blk:
                image_blocks.append(blk)
    return "\n".join(lines), image_blocks
