"""TICKET-3: shared custom form fields."""
from decimal import InvalidOperation

from wtforms import DecimalField, widgets

from app.utils.numbers import parse_decimal_loose


class LooseDecimalField(DecimalField):
    """A DecimalField that accepts Arabic digits, a decimal comma, and a % suffix.

    WTForms' stock DecimalField renders as ``<input type="number">``, and a
    number input **silently discards** anything the browser can't parse — the
    server then sees an empty string with no error, which is how TICKET-3
    ("no message appears") happened. Rendering as a text input keeps whatever
    the user typed so we can normalise it here and, if it really is garbage,
    tell them so.

    Only for small readings (percentages, analysis values). See the warning in
    app/utils/numbers.py about latin commas before using this on money fields.
    """

    widget = widgets.TextInput()

    def __init__(self, *args, invalid_message: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invalid_message = invalid_message or "اكتب رقم صحيح (مثال: 3.5)."

    def process_formdata(self, valuelist):
        if not valuelist:
            return
        try:
            self.data = parse_decimal_loose(valuelist[0])
        except InvalidOperation:
            self.data = None
            raise ValueError(self.invalid_message) from None
