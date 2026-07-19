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

from app.models.sales import Customer, CustomerPayment


CONTRACT_CHOICES = [
    (Customer.CONTRACT_DAILY, "يومي"),
    (Customer.CONTRACT_WEEKLY, "أسبوعي"),
]

PRICING_CHOICES = [
    (Customer.PRICING_FIXED, "سعر ثابت / كيلو"),
    (Customer.PRICING_QUALITY, "على أساس تحليل الجودة"),
]

PAYMENT_METHOD_CHOICES = [
    (CustomerPayment.METHOD_CASH, "كاش"),
    (CustomerPayment.METHOD_TRANSFER, "تحويل بنكي"),
]


class CustomerForm(FlaskForm):
    name = StringField(
        "اسم العميل",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    phone = StringField("رقم التليفون", validators=[Optional(), Length(max=30)])
    contract_type = SelectField("نوع العقد", choices=CONTRACT_CHOICES, validators=[DataRequired()])
    pricing_type = SelectField("طريقة التسعير", choices=PRICING_CHOICES, validators=[DataRequired()])
    fixed_price = DecimalField(
        "السعر الثابت (جنيه/كيلو) — لو سعر ثابت فقط",
        places=3,
        validators=[Optional(), NumberRange(min=0)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ")


class MilkDeliveryForm(FlaskForm):
    """Matches the client's real invoice Excel format. All adjustment columns
    are optional and default to 0. Fat/protein/bacteria bonuses can be filled
    manually or auto-computed from the quality formula in settings."""

    customer_id = SelectField("العميل", coerce=int, validators=[DataRequired()])
    delivery_date = DateField("تاريخ التوريد", validators=[DataRequired()], default=date.today)

    qty_kg = DecimalField(
        "الكمية (كيلو)",
        places=3,
        validators=[DataRequired(message="الكمية مطلوبة."), NumberRange(min=0.001)],
    )
    unit_price = DecimalField(
        "السعر (جنيه/كيلو) — اتركه فارغ لاستخدام سعر العميل الثابت",
        places=3,
        validators=[Optional(), NumberRange(min=0)],
    )

    protein_pct = DecimalField(
        "نسبة البروتين %",
        places=2,
        validators=[Optional(), NumberRange(min=0, max=15)],
    )
    bacteria_count = IntegerField(
        "عدد البكتيريا (CFU/ml)",
        validators=[Optional(), NumberRange(min=0)],
    )

    # Bonuses / adjustments (positive numbers)
    fat_bonus = DecimalField("الدهن", places=2, default=0, validators=[Optional()])
    protein_bonus = DecimalField("البروتين", places=2, default=0, validators=[Optional()])
    bacteria_adj = DecimalField("البكتيريا (تعديل)", places=2, default=0, validators=[Optional()])
    transport = DecimalField("النقل", places=2, default=0, validators=[Optional()])
    other_adj = DecimalField("أخرى", places=2, default=0, validators=[Optional()])

    # Deductions (positive numbers, subtracted)
    qty_deduction = DecimalField("خصم كمية", places=2, default=0, validators=[Optional()])
    cash_deduction = DecimalField("خصم نقدي", places=2, default=0, validators=[Optional()])
    rounding = DecimalField("كسور (±)", places=2, default=0, validators=[Optional()])

    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تسجيل التوريد")


class CustomerPaymentForm(FlaskForm):
    amount = DecimalField(
        "المبلغ المدفوع",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."), NumberRange(min=0.01)],
    )
    payment_date = DateField("تاريخ الدفع", validators=[DataRequired()], default=date.today)
    method = SelectField("طريقة الدفع", choices=PAYMENT_METHOD_CHOICES, validators=[DataRequired()])
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تسجيل الدفعة")


class DailyProductionForm(FlaskForm):
    production_date = DateField("التاريخ", validators=[DataRequired()], default=date.today)
    total_kg = DecimalField(
        "إجمالي الإنتاج اليومي (كيلو)",
        places=3,
        validators=[DataRequired(message="الإنتاج مطلوب."), NumberRange(min=0)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("حفظ")
