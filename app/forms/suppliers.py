from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    DecimalField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
    TextAreaField,
    widgets,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.suppliers import PurchaseInvoice, Supplier, SupplierPayment


class MultiCheckboxField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


CATEGORY_CHOICES = [
    (Supplier.CAT_FEED, "علف / مادة خام"),
    (Supplier.CAT_MEDICINE, "دواء بيطري"),
    (Supplier.CAT_OTHER, "أخرى"),
]

PAYMENT_TYPE_CHOICES = [
    (PurchaseInvoice.PAY_CASH, "نقدي (مدفوع فوراً)"),
    (PurchaseInvoice.PAY_CREDIT, "آجل (على الحساب)"),
]

PAYMENT_METHOD_CHOICES = [
    (SupplierPayment.METHOD_CASH, "كاش"),
    (SupplierPayment.METHOD_TRANSFER, "تحويل بنكي"),
]


class SupplierForm(FlaskForm):
    name = StringField(
        "اسم المورد",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    phone = StringField("رقم التليفون", validators=[Optional(), Length(max=30)])
    supplied_categories = MultiCheckboxField(
        "نوع المواد اللي بيوردها",
        choices=CATEGORY_CHOICES,
        validators=[DataRequired(message="اختر نوع واحد على الأقل.")],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ")


class SupplierPaymentForm(FlaskForm):
    amount = DecimalField(
        "المبلغ المدفوع",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."), NumberRange(min=0.01)],
    )
    payment_date = DateField("تاريخ الدفع", validators=[DataRequired()], default=date.today)
    method = SelectField("طريقة الدفع", choices=PAYMENT_METHOD_CHOICES, validators=[DataRequired()])
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    confirm_overpay = SelectField(
        "تأكيد",
        choices=[("0", ""), ("1", "1")],
        default="0",
        validators=[Optional()],
    )
    submit = SubmitField("تسجيل الدفعة")


class PurchaseInvoiceForm(FlaskForm):
    """The invoice header. Line items are parsed dynamically from request.form."""

    supplier_id = SelectField("المورد", coerce=int, validators=[DataRequired()])
    invoice_date = DateField("تاريخ الفاتورة", validators=[DataRequired()], default=date.today)
    payment_type = SelectField(
        "نوع الدفع",
        choices=PAYMENT_TYPE_CHOICES,
        validators=[DataRequired()],
        default=PurchaseInvoice.PAY_CASH,
    )
    original_invoice_no = StringField(
        "رقم الفاتورة الأصلي من المورد (اختياري)",
        validators=[Optional(), Length(max=80)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ الفاتورة")
