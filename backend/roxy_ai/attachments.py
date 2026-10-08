"""Attachment storage and conversion for each AI.

Files are copied into the app's data folder under a random id; the GUI and
the APIs refer to them by id only, so no arbitrary local path is ever read or
served on request. Images are converted to a format/size the target AI
accepts; anything an AI cannot take is reported with the reason.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .adapters.base import InputPart
from .db import Database, new_id

MAX_UPLOAD = 100 * 1024 * 1024
MAX_INLINE_TEXT = 200 * 1024
TEXT_EXTS = {
    ".txt", ".md", ".py", ".json", ".csv", ".tsv", ".yaml", ".yml", ".toml", ".ini", ".xml", ".html",
    ".js", ".ts", ".tsx", ".css", ".glsl", ".osl", ".log", ".obj", ".mtl", ".usda",
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".tga", ".exr", ".hdr"}
THUMB_EDGE = 320
Image.MAX_IMAGE_PIXELS = 20000 * 20000


def safe_name(name: str) -> str:
    name = Path(name).name  # drop any directory part
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:120] or "file"


@dataclass
class Prepared:
    parts: list[InputPart] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)


class AttachmentStore:
    def __init__(self, db: Database, root: Path):
        self._db = db
        self._root = root

    def _dir(self, aid: str) -> Path:
        return self._root / aid[:2] / aid

    def save_bytes(self, data: bytes, name: str, mime: str, kind: str, conversation_id: str | None = None) -> dict:
        if len(data) > MAX_UPLOAD:
            raise ValueError(f"ファイルが大きすぎます（上限 {MAX_UPLOAD // 1024 // 1024}MB）")
        aid = new_id()
        folder = self._dir(aid)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe_name(name)
        path.write_bytes(data)
        mime = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if mime.startswith("image/") or path.suffix.lower() in IMAGE_EXTS:
            self._thumbnail(path, folder / "thumb.png")
        return self._db.add_attachment(
            id=aid, conversation_id=conversation_id, kind=kind, name=path.name, mime=mime,
            size=len(data), sha256=hashlib.sha256(data).hexdigest(), path=str(path),
        )

    async def save_upload(self, upload, conversation_id: str | None = None) -> dict:
        data = await upload.read(MAX_UPLOAD + 1)
        return self.save_bytes(data, upload.filename or "file", upload.content_type or "", "upload", conversation_id)

    @staticmethod
    def _thumbnail(src: Path, dst: Path) -> None:
        try:
            with Image.open(src) as im:
                im.thumbnail((THUMB_EDGE, THUMB_EDGE))
                if im.mode not in ("RGB", "RGBA"):
                    im = im.convert("RGBA")
                im.save(dst, "PNG")
        except (UnidentifiedImageError, OSError, ValueError):
            pass

    def file_for(self, att: dict, thumb: bool = False) -> Path | None:
        path = Path(att["path"])
        # Only ever serve files inside our own attachment folder.
        try:
            path.resolve().relative_to(self._root.resolve())
        except ValueError:
            return None
        if thumb:
            t = path.parent / "thumb.png"
            if t.is_file():
                return t
        return path if path.is_file() else None

    def delete_files(self, path: str) -> None:
        folder = Path(path).parent
        try:
            folder.resolve().relative_to(self._root.resolve())
        except ValueError:
            return
        shutil.rmtree(folder, ignore_errors=True)

    def delete(self, aid: str) -> None:
        path = self._db.delete_attachment(aid)
        if path:
            self.delete_files(path)

    # Conversion for AIs ----------------------------------------------------------

    def _ai_image(self, src: Path, mimes: set[str], max_edge: int) -> tuple[Path, str]:
        """Return a copy the AI accepts: allowed format, longest edge <= max_edge, < 5MB."""
        with Image.open(src) as im:
            im.load()
            fmt_mime = Image.MIME.get(im.format or "", "")
            needs_resize = max(im.size) > max_edge
            if fmt_mime in mimes and not needs_resize and src.stat().st_size < 4_500_000:
                return src, fmt_mime
            out_path = src.parent / f"ai_{max_edge}.png"
            if out_path.is_file():
                return out_path, "image/png"
            if needs_resize:
                im.thumbnail((max_edge, max_edge), Image.LANCZOS)
            if im.mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA")
            im.save(out_path, "PNG", optimize=True)
            if out_path.stat().st_size > 4_500_000 and "image/jpeg" in mimes:
                jpg = src.parent / f"ai_{max_edge}.jpg"
                im.convert("RGB").save(jpg, "JPEG", quality=90)
                out_path.unlink(missing_ok=True)
                return jpg, "image/jpeg"
            return out_path, "image/png"

    def prepare(self, attachments: list[dict], *, supports_images: bool, mimes: set[str], max_edge: int,
                provider_name: str) -> Prepared:
        prep = Prepared()
        for att in attachments:
            path = self.file_for(att)
            if path is None:
                prep.notices.append(f"「{att['name']}」が見つからないため送信できませんでした。")
                continue
            ext = path.suffix.lower()
            is_image = att["mime"].startswith("image/") or ext in IMAGE_EXTS
            if is_image:
                if not supports_images:
                    prep.notices.append(
                        f"「{att['name']}」: 選択中の {provider_name} モデルは画像入力に対応していないため、画像は渡さずファイルパスのみ伝えます。"
                    )
                    prep.parts.append(InputPart("text", text=f"[添付画像（モデル非対応のためパスのみ）: {path}]"))
                    continue
                try:
                    ai_path, mime = self._ai_image(path, mimes, max_edge)
                except (UnidentifiedImageError, OSError, ValueError) as e:
                    prep.notices.append(f"「{att['name']}」は画像として読み込めないため、ファイルパスとして渡します（{e}）。")
                    prep.parts.append(InputPart("text", text=f"[添付ファイル: {path}]"))
                    continue
                if ai_path != path:
                    prep.notices.append(f"「{att['name']}」は {provider_name} 向けに変換・縮小して送信しました（元ファイルは保持）。")
                prep.parts.append(InputPart("text", text=f"[添付画像: {att['name']} — 元ファイル: {path}]"))
                prep.parts.append(InputPart("image", path=str(ai_path), mime=mime))
            elif ext in TEXT_EXTS and att["size"] <= MAX_INLINE_TEXT:
                try:
                    content = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    content = path.read_text(encoding="cp932", errors="replace")
                prep.parts.append(InputPart("text", text=f"[添付ファイル: {att['name']}（{path}）]\n```\n{content}\n```"))
            else:
                # .blend / .fbx / .pdf etc.: the AI can't take the bytes inline, but can
                # use the path with its own tools (e.g. the MCP's import tools).
                prep.notices.append(
                    f"「{att['name']}」は {provider_name} に直接入力できない形式のため、ローカルパスとして渡しました。"
                )
                prep.parts.append(InputPart("text", text=f"[添付ファイル（パス参照）: {path}]"))
        return prep
