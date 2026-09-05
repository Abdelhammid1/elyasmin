"""ACCOUNTING P2 — forms for the manual JE + pause/reactivate actions.
PHASE 31 (FIN-6) adds LedgerAccountForm for the CoA CRUD UI."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField, DateField, SelectField, StringField, SubmitField, TextAreaField,
)
from wtforms.validators import DataRequired, Length, Optional


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


# ==================== PHASE 31 — FIN-6: CoA CRUD ====================

ACCOUNT_TYPE_CHOICES = [
    ("ASSET",     "أصول"),
    ("LIABILITY", "خصوم"),
    ("EQUITY",    "حقوق ملكية"),
    ("REVENUE",   "إيرادات"),
    ("EXPENSE",   "مصروفات"),
]


class LedgerAccountForm(FlaskForm):
    """Create/edit a LedgerAccount. Type is required only when parent_id
    is 0 (root); otherwise it's inherited from the parent (route enforces
    this — the type SelectField is disabled by JS but the route ignores
    the posted value when a parent is chosen).

    `parent_id` and `treasury_account_id` use `coerce=int` with a
    sentinel value of 0 meaning "none" — WTForms doesn't play nicely
    with a nullable int SelectField so we translate 0 → None in the
    route.
    """

    code = StringField(
        "الكود",
        validators=[DataRequired(message="الكود مطلوب."), Length(max=20)],
    )
    name = StringField(
        "الاسم بالعربي",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=150)],
    )
    name_en = StringField(
        "الاسم بالإنجليزي (اختياري)",
        validators=[Optional(), Length(max=150)],
    )
    type = SelectField(
        "النوع",
        choices=ACCOUNT_TYPE_CHOICES,
        validators=[DataRequired()],
    )
    parent_id = SelectField(
        "الحساب الأب",
        coerce=int,
        validators=[Optional()],
        default=0,
    )
    is_postable = BooleanField(
        "يقبل قيود مباشرة (leaf) — بدون تعليم = تجميعي (header)",
        default=True,
    )
    treasury_account_id = SelectField(
        "خزنة/بنك مربوطة (اختياري)",
        coerce=int,
        validators=[Optional()],
        default=0,
    )
    submit = SubmitField("حفظ")
