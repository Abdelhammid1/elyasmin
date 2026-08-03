"""TICKET-3: tolerant numeric parsing for Arabic user input.

The app is Arabic RTL and the staff type on Arabic keyboards, so a "number"
field realistically receives any of:

    "3.5"     plain latin
    "3.5%"    with the unit they see on the label
    "٣٫٥"     Arabic-Indic digits + Arabic decimal separator
    "۳٫۵"     Extended Arabic-Indic (Persian) digits
    "3,5"     decimal comma
    " 3.5 "   stray whitespace

`parse_decimal_loose` normalises all of these to a Decimal. Use it via
`LooseDecimalField` (app/forms/fields.py) rather than calling it directly in a
route, so the error surfaces as a proper form error.

⚠️ A latin comma is read as a DECIMAL separator, so "1,200" parses as 1.2 — not
1200. That is the right call for small readings like a protein percentage
(0–15), where nobody writes a thousands separator. Do NOT reuse this for money
or quantity fields, where "1,200" almost certainly means one thousand two
hundred; those need a locale-aware parser, not this one.
"""
from decimal import Decimal, InvalidOperation

# Arabic-Indic (U+0660–0669) and Extended Arabic-Indic / Persian (U+06F0–06F9)
_DIGIT_MAP = {ord("٠") + i: str(i) for i in range(10)}
_DIGIT_MAP.update({ord("۰") + i: str(i) for i in range(10)})

# Arabic decimal separator (٫) and thousands separator (٬)
_SEPARATORS = {
    "٫": ".",  # ٫ decimal
    "٬": "",   # ٬ thousands
    ",": ".",
}

_STRIP_SUFFIX = ("%", "٪")  # latin % and Arabic ٪


def parse_decimal_loose(raw) -> Decimal | None:
    """Parse a user-typed number, tolerating Arabic digits and a % suffix.

    Returns None for blank input. Raises InvalidOperation for genuine garbage,
    so the caller can turn it into a validation message.
    """
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw

    text = str(raw).strip()
    if not text:
        return None

    text = text.translate(_DIGIT_MAP)
    for src, dst in _SEPARATORS.items():
        text = text.replace(src, dst)
    for suffix in _STRIP_SUFFIX:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()

    # A lone separator or sign is not a number
    if not text or text in {".", "-", "+", "-.", "+."}:
        raise InvalidOperation(f"not a number: {raw!r}")

    try:
        return Decimal(text)
    except InvalidOperation:
        raise InvalidOperation(f"not a number: {raw!r}") from None
