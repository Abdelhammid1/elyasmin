"""SEC-3 (PHASE 29): user-uploaded file validation.

Currently only one upload site exists (the company-logo picker in
`finance/settings`), so this module is deliberately narrow — one
`validate_logo(file_storage)` helper. When a second upload appears,
grow this into a shared allowlist API; premature abstraction avoided.

Defense-in-depth against three classes of bad input:
  1. Wrong extension     — client sends `.svg` / `.html`.
  2. Wrong content       — an HTML file renamed `.png` (magic-number
                            sniff on the first 12 bytes catches it).
  3. Oversize            — 2 MB cap, checked before .save() so a
                            50 MB blob can't land on disk.

There is also a global `MAX_CONTENT_LENGTH = 3 MB` in `config.py`
that acts as a Werkzeug-level backstop: any request body larger than
that hits a 413 before ever reaching a view function. This helper
handles the same-magnitude case where the body is under the global
cap but the SINGLE FILE is too big for a logo.
"""
from __future__ import annotations

ALLOWED_LOGO_EXTS = {"png", "jpg", "jpeg", "webp"}
MAX_LOGO_SIZE = 2 * 1024 * 1024  # 2 MB, per ticket

# First 12 bytes are enough to fingerprint every allowed format.
_PNG_SIG  = b"\x89PNG\r\n\x1a\n"    # 8 bytes
_JPEG_SIG = b"\xff\xd8\xff"          # 3 bytes
# WebP: "RIFF" at [0:4], any 4 size bytes, "WEBP" at [8:12].


def validate_logo(file_storage) -> tuple[bool, str, str]:
    """Validate a company-logo upload.

    Returns `(ok, ext, error_ar)`:
      - `ok`     — True only if extension AND size AND magic match.
      - `ext`    — normalized extension (jpg → jpeg) on success; the
                   raw extension attempt on failure (useful for logs).
      - `error_ar` — a short Arabic message ready to `flash("...",
                   "error")`; empty string on success.

    Never raises. Consumes the file's stream to sniff header bytes,
    then rewinds it to position 0 so the caller can still `.save()`.
    """
    name = (file_storage.filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if ext == "jpg":
        ext = "jpeg"

    if ext not in ALLOWED_LOGO_EXTS:
        return False, ext, "الامتداد غير مسموح. المسموح: PNG, JPG, JPEG, WEBP."

    # Size — read to end, restore position.
    stream = file_storage.stream
    stream.seek(0, 2)          # SEEK_END
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        return False, ext, "الملف فارغ."
    if size > MAX_LOGO_SIZE:
        return False, ext, "حجم الملف أكبر من 2 ميجابايت."

    # Magic-number sniff.
    head = stream.read(12)
    stream.seek(0)
    ok = (
        (ext == "png"  and head.startswith(_PNG_SIG))            or
        (ext == "jpeg" and head.startswith(_JPEG_SIG))           or
        (ext == "webp" and head[:4] == b"RIFF" and head[8:12] == b"WEBP")
    )
    if not ok:
        return False, ext, "الملف مش صورة فعلية — الامتداد لا يطابق المحتوى."
    return True, ext, ""
