from datetime import date

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
    submit = SubmitField("حفظ الإعدادات")


class ReportFilterForm(FlaskForm):
    date_from = DateField("من تاريخ", validators=[DataRequired()])
    date_to = DateField("إلى تاريخ", validators=[DataRequired()])
    submit = SubmitField("عرض التقرير")

    class Meta:
        csrf = False
