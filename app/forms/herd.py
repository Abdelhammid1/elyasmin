from datetime import date

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
    name = StringField("الاسم (اختياري)", validators=[Optional(), Length(max=80)])
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


class SaleForm(FlaskForm):
    sale_date = DateField("تاريخ البيع", validators=[DataRequired()], default=date.today)
    buyer_name = StringField(
        "اسم المشتري", validators=[DataRequired(message="اسم المشتري مطلوب."), Length(max=120)]
    )
    price = DecimalField(
        "السعر",
        places=2,
        validators=[DataRequired(message="السعر مطلوب."), NumberRange(min=0.01)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("تسجيل البيع")


class CowSearchForm(FlaskForm):
    q = StringField("بحث برقم الأذن أو الاسم", validators=[Optional()])
    group_id = SelectField("المجموعة", coerce=int, validators=[Optional()])
    status = SelectField(
        "الحالة",
        choices=[
            ("active", "نشط"),
            ("sold", "مباع"),
            ("dead", "نافق"),
            ("all", "الكل"),
        ],
        default="active",
    )
    submit = SubmitField("بحث")

    class Meta:
        csrf = False


# ==================== HERD-2 Part 1 (PHASE 35): breeding form ====================

from app.models.herd import BreedingEvent

BREEDING_EVENT_CHOICES = [
    (BreedingEvent.EVENT_INSEMINATION,    "تلقيح"),
    (BreedingEvent.EVENT_PREGNANCY_CHECK, "جس"),
    (BreedingEvent.EVENT_DRYING,          "تجفيف"),
    (BreedingEvent.EVENT_WAITING,         "انتظار"),
    (BreedingEvent.EVENT_CALVING,         "ولادة"),
    (BreedingEvent.EVENT_WEANING,         "فطام"),
]

BREEDING_RESULT_CHOICES = [
    ("",                                    "— لا ينطبق —"),
    (BreedingEvent.RESULT_PREGNANT,         "عشار"),
    (BreedingEvent.RESULT_NOT_PREGNANT,     "مش عشار"),
]


class BreedingEventForm(FlaskForm):
    """HERD-2 Part 1: record a single reproductive-cycle event on a
    cow. Submitting this form ONLY writes the event — the
    breeding_status change requires a second explicit confirmation
    on the follow-up page."""

    event_type = SelectField(
        "نوع الإجراء",
        choices=BREEDING_EVENT_CHOICES,
        validators=[DataRequired(message="اختار نوع الإجراء.")],
    )
    event_date = DateField(
        "التاريخ",
        default=date.today,
        validators=[DataRequired()],
    )
    result = SelectField(
        "نتيجة الجس (اختياري لغير الجس)",
        choices=BREEDING_RESULT_CHOICES,
        validators=[Optional()],
    )
    notes = TextAreaField(
        "ملاحظات",
        validators=[Optional(), Length(max=1000)],
    )
    submit = SubmitField("سجل الإجراء")
