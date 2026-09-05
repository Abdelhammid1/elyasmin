"""ACCOUNTING P2 — forms for the manual JE + pause/reactivate actions.
PHASE 31 (FIN-6) adds LedgerAccountForm for the CoA CRUD UI.
PHASE 32 (FIN-7) adds QuickEntryForm for the /accounting/quick-entry page."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField, DateField, DecimalField, HiddenField, SelectField,
    StringField, SubmitField, TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


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


# ==================== PHASE 32 — FIN-7: quick-entry ====================

# The 7 supported operations. Each maps to a lines-builder in
# `app/blueprints/accounting/routes.py`. The form fields below are the
# UNION of every field any template needs; each per-kind template
# renders only the ones it uses, and the route validates the required
# subset before calling `post_journal`.
QUICK_ENTRY_KINDS = [
    ("opening",       "رصيد افتتاحي"),
    ("capital",       "زيادة رأس المال"),
    ("deposit_in",    "استلام أمانة من طرف"),
    ("deposit_out",   "رد أمانة لطرف"),
    ("drawings",      "مسحوبات المالك"),
    ("loan_received", "استلام قرض"),
    ("loan_repaid",   "سداد قسط قرض"),
]


class QuickEntryForm(FlaskForm):
    """FIN-7: one form supporting all 7 quick-entry templates. The
    `kind` HiddenField picks which lines-builder runs; the route
    enforces per-kind required fields.

    Common fields (every kind uses):
      amount, entry_date, memo

    Per-kind fields (route validates the required subset):
      account_id           — opening balance (which A/L account
                              carried the opening)
      treasury_account_id  — every kind except opening (which cash-
                              box the money went in/out)
      party_name           — deposit_in, deposit_out (free-text —
                              stored in memo, no Party model exists)
      loan_kind            — loan_received ('short' | 'long')
      loan_account_id      — loan_repaid (dropdown of 2041/2042)
    """
    kind = HiddenField(validators=[DataRequired()])

    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."),
                    NumberRange(min=0.01, message="لازم أكبر من صفر.")],
    )
    entry_date = DateField(
        "التاريخ",
        default=date.today,
        validators=[DataRequired()],
    )
    memo = StringField(
        "ملاحظة (اختياري)",
        validators=[Optional(), Length(max=200)],
    )

    account_id = SelectField(
        "الحساب",
        coerce=int,
        validators=[Optional()],   # required only for opening; route enforces
    )
    treasury_account_id = SelectField(
        "الخزنة / البنك",
        coerce=int,
        validators=[Optional()],
    )
    party_name = StringField(
        "اسم الطرف",
        validators=[Optional(), Length(max=150)],
    )
    loan_kind = SelectField(
        "نوع القرض",
        choices=[("short", "قصير الأجل"), ("long", "طويل الأجل")],
        validators=[Optional()],
    )
    loan_account_id = SelectField(
        "القرض",
        coerce=int,
        validators=[Optional()],
    )
    submit = SubmitField("حفظ القيد")
