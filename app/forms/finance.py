from datetime import date
from decimal import Decimal

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileSize
from wtforms import DateField, DecimalField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.finance import Expense


EXPENSE_CATEGORY_CHOICES = [
    (Expense.CAT_ELECTRICITY, "كهرباء"),
    (Expense.CAT_MAINTENANCE, "صيانة"),
    (Expense.CAT_RENT, "إيجار"),
    (Expense.CAT_OTHER, "أخرى"),
    ("__custom__", "➕ نوع جديد (اكتبه)"),
]


class ExpenseForm(FlaskForm):
    category = SelectField("النوع", choices=EXPENSE_CATEGORY_CHOICES, validators=[DataRequired()])
    custom_category = StringField(
        "اسم النوع الجديد",
        validators=[Optional(), Length(max=40)],
    )
    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."), NumberRange(min=0.01)],
    )
    expense_date = DateField("التاريخ", validators=[DataRequired()], default=date.today)
    # TREASURY: every expense names the account the money left
    account_id = SelectField("من حساب", coerce=int, validators=[DataRequired(message="اختار الحساب اللي الفلوس هتطلع منه.")])
    description = StringField("الوصف", validators=[Optional(), Length(max=255)])
    submit = SubmitField("حفظ")


class SettingsForm(FlaskForm):
    cost_split_milk_pct = DecimalField(
        "نسبة تحميل التكاليف غير المباشرة على مجموعة الحليب (%)",
        places=2,
        validators=[DataRequired(), NumberRange(min=0, max=100)],
    )
    cost_split_others_pct = DecimalField(
        "نسبة تحميل التكاليف غير المباشرة على باقي المجموعات (%)",
        places=2,
        validators=[DataRequired(), NumberRange(min=0, max=100)],
    )
    quality_price_base = DecimalField(
        "سعر أساس اللبن بالتحليل (جنيه/كيلو)",
        places=3,
        validators=[DataRequired(), NumberRange(min=0)],
    )
    quality_protein_adj = DecimalField(
        "زيادة السعر لكل +1% بروتين فوق 3.0%",
        places=3,
        validators=[DataRequired(), NumberRange(min=0)],
    )
    quality_bacteria_penalty = DecimalField(
        "خصم لكل +100 ألف بكتيريا/مل فوق 100 ألف",
        places=3,
        validators=[DataRequired(), NumberRange(min=0)],
    )
    # TICKET-A: fat joins the formula. Ships at 0 so nothing reprices until the
    # client puts his own rate in — DataRequired would reject that 0, so the
    # validator has to be Optional with the zero supplied as the default.
    quality_fat_ref = DecimalField(
        "نسبة الدهن اللي الزيادة بتبدأ فوقها (%)",
        places=2,
        default=Decimal("3.0"),
        validators=[Optional(), NumberRange(min=0, max=15)],
    )
    quality_fat_adj = DecimalField(
        "زيادة السعر لكل +1% دهن فوق النسبة دي (0 = الدهن مش بيأثر على السعر)",
        places=3,
        default=Decimal("0"),
        validators=[Optional(), NumberRange(min=0)],
    )
    submit = SubmitField("حفظ الإعدادات")


class ReportFilterForm(FlaskForm):
    date_from = DateField("من تاريخ", validators=[DataRequired()])
    date_to = DateField("إلى تاريخ", validators=[DataRequired()])
    submit = SubmitField("عرض التقرير")

    class Meta:
        csrf = False


ACCOUNT_TYPE_CHOICES = [
    ("cash", "خزنة نقدية"),
    ("bank", "حساب بنكي"),
]


class AccountForm(FlaskForm):
    """TREASURY: any number of accounts — add a new bank whenever you need one."""

    name = StringField(
        "اسم الحساب",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    account_type = SelectField("النوع", choices=ACCOUNT_TYPE_CHOICES, validators=[DataRequired()])
    bank_name = StringField(
        "اسم البنك (للحساب البنكي)", validators=[Optional(), Length(max=120)]
    )
    account_number = StringField("رقم الحساب", validators=[Optional(), Length(max=60)])
    opening_balance = DecimalField(
        "الرصيد الافتتاحي",
        places=2, default=0,
        validators=[Optional(), NumberRange(min=0, message="الرصيد الافتتاحي لازم يكون صفر أو أكبر.")],
    )
    submit = SubmitField("حفظ")


class AccountTransferForm(FlaskForm):
    from_account_id = SelectField("من حساب", coerce=int, validators=[DataRequired()])
    to_account_id = SelectField("إلى حساب", coerce=int, validators=[DataRequired()])
    amount = DecimalField(
        "المبلغ",
        places=2,
        validators=[DataRequired(message="المبلغ مطلوب."), NumberRange(min=0.01)],
    )
    transfer_date = DateField("تاريخ التحويل", validators=[DataRequired()], default=date.today)
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تنفيذ التحويل")


# ==================== PHASE 11 — company profile ====================

class CompanyProfileForm(FlaskForm):
    """YAS-SET-2: fills the CompanyProfile singleton row.

    Every field is Optional except `name` — the client fills in what
    they have; missing pieces just don't render on the invoice."""

    # ---- Public identity ----
    name = StringField("اسم الشركة (الاسم التجاري)",
                        validators=[DataRequired(), Length(max=150)])
    logo = FileField(
        "شعار الشركة (PNG/JPG/SVG، ≤ 2 ميجا)",
        validators=[
            Optional(),
            FileAllowed(["png", "jpg", "jpeg", "svg"],
                        "امتداد الملف لازم يكون PNG أو JPG أو SVG."),
            FileSize(max_size=2 * 1024 * 1024, message="الملف أكبر من 2 ميجا."),
        ],
    )
    base_currency = SelectField(
        "العملة الأساسية",
        choices=[("EGP", "جنيه مصري (EGP)"),
                 ("SAR", "ريال سعودي (SAR)"),
                 ("USD", "دولار (USD)"),
                 ("EUR", "يورو (EUR)")],
        default="EGP",
    )
    tax_rate_pct = DecimalField(
        "نسبة الضريبة الافتراضية %",
        places=2, default=0,
        validators=[Optional(), NumberRange(min=0, max=100)],
    )
    region = StringField("المنطقة / المحافظة",
                          validators=[Optional(), Length(max=120)])

    # ---- Legal identity — printed on the invoice ----
    legal_name = StringField(
        "الاسم القانوني (الاسم الرسمي على الفاتورة)",
        validators=[Optional(), Length(max=200)],
    )
    commercial_register_no = StringField(
        "رقم السجل التجاري",
        validators=[Optional(), Length(max=60)],
    )
    tax_registration_no = StringField(
        "الرقم الضريبي (البطاقة الضريبية)",
        validators=[Optional(), Length(max=60)],
    )
    address = TextAreaField("العنوان",
                             validators=[Optional(), Length(max=500)])

    # ---- Bank info ----
    bank_account_holder = StringField(
        "اسم صاحب الحساب",
        validators=[Optional(), Length(max=150)],
    )
    bank_name = StringField("اسم البنك",
                             validators=[Optional(), Length(max=150)])
    bank_account_no = StringField(
        "رقم الحساب",
        validators=[Optional(), Length(max=60)],
    )
    bank_iban = StringField("رقم IBAN",
                             validators=[Optional(), Length(max=60)])

    # ---- Invoice numbering (YAS-SET-4) ----
    invoice_number_prefix_sale = StringField(
        "بادئة رقم فواتير البيع",
        default="INV",
        validators=[DataRequired(), Length(max=20)],
    )
    invoice_number_prefix_purchase = StringField(
        "بادئة رقم فواتير الشراء",
        default="PUR",
        validators=[DataRequired(), Length(max=20)],
    )

    # ---- Operational (dormant — reminders come later) ----
    reminder_days_before_due = IntegerField(
        "إشعار الاستحقاق قبله بكام يوم",
        default=3,
        validators=[Optional(), NumberRange(min=0, max=90)],
    )

    submit = SubmitField("حفظ بيانات الشركة")
