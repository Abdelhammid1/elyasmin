from datetime import date
from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    DecimalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.herd import Birth, CattleGroup, Cow, Death


GROUP_TYPE_CHOICES = [
    (CattleGroup.TYPE_MILK, "حليب"),
    (CattleGroup.TYPE_DRY, "جفاف"),
    (CattleGroup.TYPE_PRE_BIRTH, "انتظار ولادة"),
    (CattleGroup.TYPE_NURSING, "رضاعة"),
    (CattleGroup.TYPE_FATTENING, "تسمين"),
    (CattleGroup.TYPE_CUSTOM, "مخصصة"),
]


GENDER_CHOICES = [
    (Cow.GENDER_FEMALE, "أنثى (بقرة)"),
    (Cow.GENDER_MALE, "ذكر (عجل تسمين)"),
]

DELIVERY_CHOICES = [
    (Birth.DELIVERY_NATURAL, "طبيعية"),
    (Birth.DELIVERY_HARD, "صعبة"),
    (Birth.DELIVERY_DEAD, "نافق"),
]

DEATH_REASON_CHOICES = [
    (Death.REASON_DISEASE, "مرض"),
    (Death.REASON_ACCIDENT, "حادثة"),
    (Death.REASON_UNKNOWN, "غير معروف"),
]


class GroupForm(FlaskForm):
    name = StringField(
        "اسم المجموعة",
        validators=[DataRequired(message="اسم المجموعة مطلوب."), Length(max=80)],
    )
    type = SelectField(
        "نوع المجموعة",
        choices=GROUP_TYPE_CHOICES,
        validators=[DataRequired()],
    )
    description = StringField(
        "الوصف (اختياري)",
        validators=[Optional(), Length(max=255)],
    )
    submit = SubmitField("حفظ")


class CowForm(FlaskForm):
    ear_tag = StringField(
        "رقم الأذن (Ear Tag)",
        validators=[DataRequired(message="رقم الأذن مطلوب."), Length(max=50)],
    )
    # HERD-4: the farm identifies cows by number only — no name field.
    # Status (HERD-3 list) is set right at entry for cows already on
    # the farm; choices are filled in the route.
    breeding_status = SelectField("الحالة", validators=[Optional()])
    date_of_birth = DateField(
        "تاريخ الميلاد",
        validators=[Optional()],
        default=None,
    )
    gender = SelectField("الجنس", choices=GENDER_CHOICES, validators=[DataRequired()])
    group_id = SelectField("المجموعة", coerce=int, validators=[DataRequired()])
    # TICKET-1: the "خنثة" case — a female twinned with a male. She counts as a
    # female in the herd records (not in breeding or milk figures) but is fed and
    # costed with التسمين, so she needs a deliberate way into that group.
    allow_fattening_override = BooleanField(
        "حالة خاصة (خنثة) — اسمح بوضعها في التسمين رغم إنها أنثى"
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    # SEASON-OFFSET (PHASE 37): manual backfill for calvings that
    # happened BEFORE the cow was registered in the system. The
    # field is visible on every cow form; the hint carries the
    # intent (leave at 0 for cows born inside the system).
    season_count_offset = IntegerField(
        "عدد المواسم قبل تسجيلها في النظام (اختياري)",
        validators=[Optional(),
                    NumberRange(min=0, max=50,
                                message="من 0 لحد 50 موسم.")],
        default=0,
    )
    submit = SubmitField("حفظ")


class CowMoveForm(FlaskForm):
    to_group_id = SelectField("المجموعة الجديدة", coerce=int, validators=[DataRequired()])
    moved_on = DateField("تاريخ النقل", validators=[DataRequired()], default=date.today)
    reason = StringField("سبب النقل", validators=[Optional(), Length(max=255)])
    submit = SubmitField("تأكيد النقل")


class BirthForm(FlaskForm):
    mother_id = SelectField("الأم", coerce=int, validators=[DataRequired()])
    birth_date = DateField("تاريخ الولادة", validators=[DataRequired()], default=date.today)
    calves_count = IntegerField(
        "عدد المواليد",
        validators=[DataRequired(), NumberRange(min=1, max=5, message="من 1 إلى 5.")],
        default=1,
    )
    delivery_type = SelectField(
        "حالة الولادة",
        choices=DELIVERY_CHOICES,
        validators=[DataRequired()],
        default=Birth.DELIVERY_NATURAL,
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("تسجيل الولادة")


class CalfDetailForm(FlaskForm):
    """Sub-form filled per calf on the second step of birth registration."""

    gender = SelectField("جنس المولود", choices=GENDER_CHOICES, validators=[DataRequired()])
    is_alive = SelectField(
        "الحالة",
        choices=[("1", "حي"), ("0", "نافق")],
        default="1",
        validators=[DataRequired()],
    )
    ear_tag = StringField("رقم الأذن (لو حي)", validators=[Optional(), Length(max=50)])


class DeathForm(FlaskForm):
    death_date = DateField("تاريخ النفوق", validators=[DataRequired()], default=date.today)
    reason = SelectField(
        "السبب", choices=DEATH_REASON_CHOICES, validators=[DataRequired()], default=Death.REASON_UNKNOWN
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("تسجيل النفوق")


# SALES-1: SaleForm removed — sell_cow now redirects to
# sales.new_invoice (the general SalesInvoice builder). Old
# AnimalSale rows are preserved; new ones are created inside
# on_sales_invoice for each cow line.


# ==================== HERD-2 Part 1 (PHASE 35): breeding form ====================

from app.models.herd import BreedingEvent

BREEDING_RESULT_CHOICES = [
    ("",                                    "— اختار النتيجة —"),
    (BreedingEvent.RESULT_PREGNANT,         "عشار"),
    (BreedingEvent.RESULT_NOT_PREGNANT,     "فارغة"),
]


class BreedingEventForm(FlaskForm):
    """HERD-5: record an event on a cow. The new group and status are
    pre-filled from the event type's settings (JS) and can be changed
    before saving; "" means leave as is. Choices for event_type,
    new_group_id and new_status are set in the route."""

    event_type = SelectField(
        "نوع الإجراء",
        validators=[DataRequired(message="اختار نوع الإجراء.")],
    )
    event_date = DateField(
        "التاريخ",
        default=date.today,
        validators=[DataRequired()],
    )
    result = SelectField(
        "النتيجة",
        choices=BREEDING_RESULT_CHOICES,
        validators=[Optional()],
    )
    new_group_id = SelectField("المجموعة بعد الإجراء", validators=[Optional()])
    new_status = SelectField("الحالة بعد الإجراء", validators=[Optional()])
    # HERD-8: insemination cost (shown for تلقيح only).
    cost_source = SelectField(
        "تكلفة التلقيح",
        choices=[("", "بدون تكلفة"),
                 (BreedingEvent.COST_STOCK, "من المخزون (سائل منوي)"),
                 (BreedingEvent.COST_CASH, "نقدي")],
        validators=[Optional()],
    )
    cost_ingredient_id = SelectField("الصنف", validators=[Optional()])
    cost_qty = DecimalField("الكمية", places=3, default=Decimal("1"),
                            validators=[Optional(), NumberRange(min=0.001)])
    cost_amount = DecimalField("المبلغ", places=2,
                               validators=[Optional(), NumberRange(min=0.01)])
    cost_account_id = SelectField("من خزنة", validators=[Optional()])
    notes = TextAreaField(
        "ملاحظات",
        validators=[Optional(), Length(max=1000)],
    )
    submit = SubmitField("سجل الإجراء")


class EventTypeForm(FlaskForm):
    """HERD-5: add / edit an event type and what it applies.
    Status/group choices are set in the route ("" = no change)."""

    name = StringField(
        "اسم الإجراء",
        validators=[DataRequired(message="اسم الإجراء مطلوب."), Length(max=60)],
    )
    target_group_id = SelectField("ينقل البقرة لمجموعة", validators=[Optional()])
    has_result = BooleanField("الإجراء له نتيجة (عشار / فارغة) — زي الجس")
    target_status = SelectField("الحالة بعد الإجراء", validators=[Optional()])
    status_if_positive = SelectField("الحالة لو النتيجة عشار", validators=[Optional()])
    status_if_negative = SelectField("الحالة لو النتيجة فارغة", validators=[Optional()])
    sort_order = IntegerField("الترتيب", validators=[Optional()], default=0)
    submit = SubmitField("حفظ")
