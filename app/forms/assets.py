"""PHASE 8b — forms for the fixed-assets module."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    IntegerField,
    RadioField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


CATEGORY_CHOICES = [
    ("equipment", "معدات"),
    ("machinery", "آلات"),
    ("other",     "أخرى"),
]


class FixedAssetForm(FlaskForm):
    name = StringField("اسم الأصل", validators=[DataRequired(), Length(max=150)])
    category = SelectField("النوع", choices=CATEGORY_CHOICES,
                           validators=[DataRequired()], default="equipment")
    purchase_date = DateField("تاريخ الشراء", validators=[DataRequired()],
                              default=date.today)
    purchase_cost = DecimalField(
        "تكلفة الشراء", places=2,
        validators=[DataRequired(), NumberRange(min=0.01)],
    )
    salvage_value = DecimalField(
        "القيمة التخريدية", places=2,
        default=0,
        validators=[Optional(), NumberRange(min=0)],
    )
    useful_life_months = IntegerField(
        "العمر الإنتاجي (بالشهور)",
        validators=[DataRequired(), NumberRange(min=1, max=1200)],
    )
    # Cash vs credit purchase — the radio picks which of the two IDs
    # gets sent up; the route reads accordingly.
    payment_type = RadioField(
        "نوع الشراء",
        choices=[("cash", "نقدي"), ("credit", "آجل من مورد")],
        default="cash", validators=[DataRequired()],
    )
    treasury_account_id = SelectField(
        "الحساب (للنقدي)", coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
    supplier_id = SelectField(
        "المورد (للآجل)", coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ الأصل")


class PostMonthForm(FlaskForm):
    period_month = DateField(
        "الشهر", validators=[DataRequired()], default=date.today,
    )
    submit = SubmitField("ترحيل إهلاك الشهر")


class DisposeForm(FlaskForm):
    disposal_date = DateField("تاريخ التخريد / البيع",
                              validators=[DataRequired()], default=date.today)
    sale_price = DecimalField(
        "ثمن البيع (صفر لو تخريد فقط)", places=2,
        default=0,
        validators=[Optional(), NumberRange(min=0)],
    )
    sale_treasury_id = SelectField(
        "حساب استلام الثمن", coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تسجيل التخريد")
