from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.herd import AnimalSale, Birth, CattleGroup, Cow, Death


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
