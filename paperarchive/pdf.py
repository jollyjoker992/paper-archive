"""Scans -> one PDF, with the standard library only.

JPEG pages are embedded as they are (no re-compression, so the archive copy is the scan); a camera's EXIF
orientation becomes the page's /Rotate, so a phone photo of a portrait page shows upright. Other image
formats are first converted to JPEG with macOS `sips`, or Pillow (with pillow-heif for HEIC) when installed.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp", ".bmp", ".gif"}


class PdfError(Exception):
    pass


def jpeg_info(data: bytes) -> tuple[int, int, int]:
    """(width, height, components) from the JPEG frame header."""
    if data[:2] != b"\xff\xd8":
        raise PdfError("not a JPEG file")
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7 or marker == 0xFF:
            i += 2 if marker != 0xFF else 1
            continue
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height, data[i + 9]
        i += 2 + length
    raise PdfError("JPEG has no frame header")


# EXIF orientation -> clockwise rotation to show the image upright. Mirrored values (2, 4, 5, 7) come
# from front cameras, not from photographing paper; they are shown unrotated.
ROTATION = {3: 180, 6: 90, 8: 270}


def jpeg_orientation(data: bytes) -> int:
    """The EXIF orientation tag of a JPEG (1 when absent or unreadable)."""
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker, length = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
        if marker == 0xDA:                                   # start of scan: no more headers
            break
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\0\0":
            tiff = data[i + 10:i + 2 + length]
            order = "<" if tiff[:2] == b"II" else ">"
            try:
                ifd = struct.unpack(order + "I", tiff[4:8])[0]
                for k in range(struct.unpack(order + "H", tiff[ifd:ifd + 2])[0]):
                    tag, _, _, value = struct.unpack(order + "HHI4s", tiff[ifd + 2 + 12 * k:ifd + 14 + 12 * k])
                    if tag == 0x0112:
                        return struct.unpack(order + "H", value[:2])[0]
            except struct.error:
                return 1
            return 1
        i += 2 + length
    return 1


def to_jpeg(path: Path, workdir: Path) -> bytes:
    if path.suffix.lower() in (".jpg", ".jpeg"):
        return path.read_bytes()
    out = workdir / (path.stem + ".jpg")
    if shutil.which("sips"):
        done = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "90", str(path), "--out", str(out)],
                              capture_output=True, text=True)
        if done.returncode == 0 and out.is_file():
            return out.read_bytes()
    try:
        from PIL import Image, ImageOps  # type: ignore
    except ImportError:
        raise PdfError(f"cannot convert {path.name}: needs macOS `sips` or `pip install pillow` "
                       "(or scan to JPEG or PDF)") from None
    try:
        from pillow_heif import register_heif_opener  # type: ignore
        register_heif_opener()
    except ImportError:
        if path.suffix.lower() in (".heic", ".heif"):
            raise PdfError(f"cannot convert {path.name}: HEIC needs `pip install pillow-heif`") from None
    ImageOps.exif_transpose(Image.open(path)).convert("RGB").save(out, "JPEG", quality=90)
    return out.read_bytes()


def images_to_pdf(images: list[Path], out: Path) -> int:
    """Write one page per image; returns the page count."""
    objects: list[bytes] = [b"", b""]          # 1 = catalog, 2 = pages (filled in below)
    kids = []
    with tempfile.TemporaryDirectory() as tmp:
        for image in images:
            data = to_jpeg(image, Path(tmp))
            width, height, components = jpeg_info(data)
            space = {1: "/DeviceGray", 3: "/DeviceRGB", 4: "/DeviceCMYK"}.get(components)
            if not space:
                raise PdfError(f"{image.name}: unsupported JPEG colour layout")
            rotate = ROTATION.get(jpeg_orientation(data), 0)
            shown_w, shown_h = (height, width) if rotate in (90, 270) else (width, height)
            scale = min(595 / shown_w, 842 / shown_h)             # fit A4 as shown, keep the proportions
            w, h = round(width * scale, 2), round(height * scale, 2)
            decode = " /Decode [1 0 1 0 1 0 1 0]" if components == 4 else ""
            objects.append(f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace {space} "
                           f"/BitsPerComponent 8 /Filter /DCTDecode{decode} /Length {len(data)} >>\nstream\n".encode()
                           + data + b"\nendstream")
            image_no = len(objects)
            content = f"q {w} 0 0 {h} 0 0 cm /Im0 Do Q".encode()
            objects.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
            objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w} {h}] /Rotate {rotate} /Contents {image_no + 1} 0 R "
                           f"/Resources << /XObject << /Im0 {image_no} 0 R >> >> >>".encode())
            kids.append(len(objects))
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Count {len(kids)} /Kids [{' '.join(f'{k} 0 R' for k in kids)}] >>".encode()
    body, offsets = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += b"%d 0 obj\n" % number + obj + b"\nendobj\n"
    xref = len(body)
    body += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    body += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    body += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    out.write_bytes(bytes(body))
    return len(kids)
