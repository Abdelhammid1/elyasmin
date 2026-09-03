"""PHASE 8a — forms for creating checks."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class _CheckFormBase(FlaskForm):
    check_number = StringField(
        "رقم الشيك",
        validators=[DataRequired(message="رقم الشيك مطلوب."), Length(max=60)],
    )
    bank_name = StringField(
        "اسم البنك",
        validators=[DataRequired(message="اسم البنك مطلوب."), Length(max=120)],
    )
    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."),
                    NumberRange(min=0.01, message="لازم يكون أكبر من صفر.")],
    )
    issue_date = DateField("تاريخ التحرير", validators=[DataRequired()], default=date.today)
    due_date = DateField("تاريخ الاستحقاق", validators=[DataRequired()])
    related_ref = StringField(
        "مرجع (فاتورة / رقم عملية)",
        validators=[Optional(), Length(max=120)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ الشيك")


class ReceivedCheckForm(_CheckFormBase):
    customer_id = SelectField("العميل", coerce=int, validators=[DataRequired()])
    drawer_name = StringField(
        "اسم الساحب (لو غير العميل)",
        validators=[Optional(), Length(max=120)],
    )


class IssuedCheckForm(_CheckFormBase):
    supplier_id = SelectField("المورد", coerce=int, validators=[DataRequired()])


class ClearCheckForm(FlaskForm):
    """Used for received-clear and issued-settle — both need to name a
    treasury drawer and a cleared_on date."""
    treasury_account_id = SelectField("الحساب البنكي", coerce=int, validators=[DataRequired()])
    cleared_on = DateField("تاريخ الصرف", validators=[DataRequired()], default=date.today)
    submit = SubmitField("تسجيل")


class BounceCheckForm(FlaskForm):
    bounced_on = DateField("تاريخ الارتداد", validators=[DataRequired()], default=date.today)
    notes = TextAreaField("سبب الارتداد", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تسجيل الارتداد")
