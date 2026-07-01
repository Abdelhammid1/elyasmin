from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.labor import Worker, WorkerPayment


WAGE_CHOICES = [
    (Worker.WAGE_PER_BATCH, "بالحلبة"),
    (Worker.WAGE_DAILY, "يومي"),
]

REASON_CHOICES = [
    (WorkerPayment.REASON_ADVANCE, "سلفة"),
    (WorkerPayment.REASON_SALARY, "دفعة من الراتب"),
]


class WorkerForm(FlaskForm):
    name = StringField("الاسم", validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)])
    phone = StringField("رقم التليفون", validators=[Optional(), Length(max=30)])
    wage_type = SelectField("نوع الأجر", choices=WAGE_CHOICES, validators=[DataRequired()])
    rate = DecimalField(
        "السعر (جنيه)",
        places=2,
        validators=[DataRequired(message="السعر مطلوب."), NumberRange(min=0)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("حفظ")


class WorkerPaymentForm(FlaskForm):
    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."), NumberRange(min=0.01)],
    )
    payment_date = DateField("التاريخ", validators=[DataRequired()], default=date.today)
    reason = SelectField("السبب", choices=REASON_CHOICES, validators=[DataRequired()])
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تسجيل الدفعة")
