"""PHASE 5 — forms for creating sales / purchase returns."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


MODE_CHOICES = [
    ("credit", "كنوت (خصم من الرصيد)"),
    ("cash", "مرتجع نقدي"),
]


class _ReturnFormBase(FlaskForm):
    return_date = DateField("تاريخ المرتجع", validators=[DataRequired()], default=date.today)
    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."),
                    NumberRange(min=0.01, message="لازم يكون أكبر من صفر.")],
    )
    mode = SelectField("النوع", choices=MODE_CHOICES, validators=[DataRequired()], default="credit")
    treasury_account_id = SelectField(
        "حساب الخزنة / البنك (للنقدي)",
        coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
    reason = StringField("السبب", validators=[Optional(), Length(max=500)])
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ")


class SalesReturnForm(_ReturnFormBase):
    customer_id = SelectField("العميل", coerce=int, validators=[DataRequired()])
    invoice_id = SelectField(
        "الفاتورة (اختياري)",
        coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )


class PurchaseReturnForm(_ReturnFormBase):
    supplier_id = SelectField("المورد", coerce=int, validators=[DataRequired()])
    invoice_id = SelectField(
        "الفاتورة (اختياري)",
        coerce=lambda x: int(x) if x else None,
        validators=[Optional()],
    )
