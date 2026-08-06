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
    # TICKET-1: opening balance — what we already owe him before the system
    opening_balance = DecimalField(
        "رصيد افتتاحي (اللي إحنا مدينين بيه للمورد قبل ما نبدأ النظام)",
        places=2,
        default=0,
        validators=[
            Optional(),
            NumberRange(min=0, message="الرصيد الافتتاحي لازم يكون صفر أو أكبر."),
        ],
    )
    supplied_categories = MultiCheckboxField(
        "نوع المواد اللي بيوردها",
        choices=CATEGORY_CHOICES,
        validators=[DataRequired(message="اختر نوع واحد على الأقل.")],
    )
    # TICKET-1: optional link to an existing customer record
    linked_customer_id = SelectField(
        "ربط بحساب عميل (اختياري — لو نفس الشخص)",
        coerce=int, validators=[Optional()],
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
    # TREASURY: every payment names the account the money left
    account_id = SelectField("من حساب", coerce=int, validators=[DataRequired(message="اختار الحساب اللي الفلوس هتطلع منه.")])
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    confirm_overpay = SelectField(
        "تأكيد",
        choices=[("0", ""), ("1", "1")],
        default="0",
        validators=[Optional()],
    )
    submit = SubmitField("تسجيل الدفعة")


# TICKET-3: tax + discount types (Dina 2026-08-01)
# - "commercial_industrial" is ONE combined option (per Egyptian tax law)
# - "__custom__" lets the user type any name they need
TAX_TYPE_CHOICES = [
    ("vat", "ضريبة القيمة المضافة"),
    ("commercial_industrial", "ضريبة تجارية وصناعية"),
    ("__custom__", "➕ نوع آخر (اكتبه)"),
]

DISCOUNT_TYPE_CHOICES = [
    ("cash", "خصم نقدي"),
    ("quantity", "خصم كمية"),
    ("__custom__", "➕ نوع آخر (اكتبه)"),
]


class PurchaseInvoiceForm(FlaskForm):
    """Invoice header. Line items AND tax/discount charge rows are parsed
    dynamically from request.form (see purchases/routes.py)."""

    supplier_id = SelectField("المورد", coerce=int, validators=[DataRequired()])
    invoice_date = DateField("تاريخ الفاتورة", validators=[DataRequired()], default=date.today)
    payment_type = SelectField(
        "نوع الدفع",
        choices=PAYMENT_TYPE_CHOICES,
        validators=[DataRequired()],
        default=PurchaseInvoice.PAY_CASH,
    )
    # TREASURY: a CASH invoice pays out immediately, so it needs an account.
    # A credit invoice moves no money — the account is ignored there, so the
    # field is Optional here and required in the route only for cash.
    account_id = SelectField("يتدفع من حساب (للفاتورة النقدي)", coerce=int, validators=[Optional()])
    original_invoice_no = StringField(
        "رقم الفاتورة الأصلي من المورد (اختياري)",
        validators=[Optional(), Length(max=80)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ الفاتورة")
