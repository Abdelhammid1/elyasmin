from datetime import date
from decimal import Decimal

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, SelectField, StringField, SubmitField, TextAreaField
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
