#!/usr/bin/env python3
"""Regenerate Tauri bundle icons from the official web favicon.

Source of truth: <repo>/src/app/favicon.ico
Output:          apps/desktop/src-tauri/icons/*

Usage (from repo root or apps/desktop):
  python apps/desktop/scripts/generate-icons.py
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from struct import pack, unpack_from
import sys

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover
    raise SystemExit("Pillow is required: pip install Pillow") from exc

PNG_SIG = bytes.fromhex("89504e470d0a1a0a")
SCRIPT_DIR = Path(__file__).resolve().parent
DESKTOP_DIR = SCRIPT_DIR.parent
REPO_ROOT = DESKTOP_DIR.parent.parent
SOURCE_ICO = REPO_ROOT / "src" / "app" / "favicon.ico"
OUT_DIR = DESKTOP_DIR / "src-tauri" / "icons"


def load_source_rgba() -> Image.Image:
    data = SOURCE_ICO.read_bytes()
    reserved, typ, count = unpack_from("<HHH", data, 0)
    if reserved != 0 or typ != 1 or count < 1:
        raise SystemExit(f"invalid ICO header in {SOURCE_ICO}")
    # pick the largest entry
    best = None
    off = 6
    for _ in range(count):
        w, h, _colors, _res, _planes, _bpp, size, offset = unpack_from("<BBBBHHII", data, off)
        ww = 256 if w == 0 else w
        hh = 256 if h == 0 else h
        blob = data[offset : offset + size]
        best = max(best, (ww * hh, ww, hh, blob), key=lambda t: t[0]) if best else (ww * hh, ww, hh, blob)
        off += 16
    assert best is not None
    _area, ww, hh, blob = best
    if blob[:8] == PNG_SIG:
        img = Image.open(BytesIO(blob)).convert("RGBA")
    else:
        # DIB fallback not expected for our asset; keep error clear
        raise SystemExit(f"favicon entry is not PNG-compressed ({ww}x{hh})")
    if img.size != (256, 256):
        img = img.resize((256, 256), Image.Resampling.LANCZOS)
    return img


def resize(img: Image.Image, size: int) -> Image.Image:
    if size == img.size[0]:
        return img.copy()
    if size < img.size[0]:
        return img.resize((size, size), Image.Resampling.LANCZOS)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def write_pngs(img: Image.Image) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in (
        ("32x32.png", 32),
        ("128x128.png", 128),
        ("henry.w@example.net", 256),
        ("icon.png", 256),
    ):
        path = OUT_DIR / name
        resize(img, size).save(path, format="PNG", optimize=True)
        print(f"wrote {path.relative_to(DESKTOP_DIR)} ({path.stat().st_size} bytes)")


def write_ico(img: Image.Image) -> None:
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs: list[bytes] = []
    for s in sizes:
        buf = BytesIO()
        resize(img, s).save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    header_size = 6 + 16 * len(sizes)
    offset = header_size
    entries: list[tuple[int, int, int, int]] = []
    for s, blob in zip(sizes, blobs):
        wb = 0 if s == 256 else s
        hb = 0 if s == 256 else s
        entries.append((wb, hb, len(blob), offset))
        offset += len(blob)

    parts: list[bytes] = [pack("<HHH", 0, 1, len(sizes))]
    for wb, hb, nbytes, off in entries:
        parts.append(pack("<BBBBHHII", wb, hb, 0, 0, 1, 32, nbytes, off))
    parts.extend(blobs)

    path = OUT_DIR / "icon.ico"
    path.write_bytes(b"".join(parts))
    print(f"wrote {path.relative_to(DESKTOP_DIR)} ({path.stat().st_size} bytes, {len(sizes)} sizes)")


def write_icns(img: Image.Image) -> None:
    mapping = [
        (b"icp4", 16),
        (b"icp5", 32),
        (b"icp6", 64),
        (b"ic07", 128),
        (b"ic08", 256),
        (b"ic09", 512),
    ]
    body = b""
    for code, size in mapping:
        buf = BytesIO()
        resize(img, size).save(buf, format="PNG", optimize=True)
        payload = buf.getvalue()
        body += code + (len(payload) + 8).to_bytes(4, "big") + payload
    total = 8 + len(body)
    path = OUT_DIR / "icon.icns"
    path.write_bytes(b"icns" + total.to_bytes(4, "big") + body)
    print(f"wrote {path.relative_to(DESKTOP_DIR)} ({path.stat().st_size} bytes)")


def main() -> int:
    if not SOURCE_ICO.is_file():
        print(f"missing source favicon: {SOURCE_ICO}", file=sys.stderr)
        return 1
    print(f"source: {SOURCE_ICO}")
    img = load_source_rgba()
    write_pngs(img)
    write_ico(img)
    write_icns(img)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
