"""ACCOUNTING P2 — forms for the manual JE + pause/reactivate actions."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length


class ManualJournalForm(FlaskForm):
    """Header for a manual JE. Line rows are parsed straight from
    request.form because they're a dynamic list.
    """

    entry_date = DateField(
        "تاريخ القيد",
        validators=[DataRequired()],
        default=date.today,
    )
    description = StringField(
        "الوصف",
        validators=[DataRequired(message="لازم تكتب وصف للقيد."),
                    Length(max=500)],
    )
    reference = StringField(
        "مرجع (اختياري)",
        validators=[Length(max=50)],
    )
    submit = SubmitField("حفظ القيد")


class PauseReasonForm(FlaskForm):
    """One-field form for both pause and reactivate — the label is set in
    the template."""

    reason = TextAreaField(
        "السبب",
        validators=[DataRequired(message="لازم تكتب السبب."), Length(max=500)],
    )
    submit = SubmitField("تنفيذ")
