from flask_wtf import FlaskForm
from wtforms import DecimalField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.models.inventory import Ingredient


CATEGORY_CHOICES = [
    (Ingredient.CATEGORY_FEED, "علف / مادة خام"),
    (Ingredient.CATEGORY_MEDICINE, "دواء بيطري"),
]

UNIT_CHOICES = [
    (Ingredient.UNIT_KG, "كيلو"),
    (Ingredient.UNIT_LITRE, "لتر"),
    (Ingredient.UNIT_PIECE, "قطعة"),
    (Ingredient.UNIT_BOX, "علبة"),
]


class IngredientForm(FlaskForm):
    name = StringField(
        "اسم المادة",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    category = SelectField("النوع", choices=CATEGORY_CHOICES, validators=[DataRequired()])
    unit = SelectField("وحدة القياس", choices=UNIT_CHOICES, validators=[DataRequired()])
    min_qty = DecimalField(
        "الحد الأدنى",
        places=3,
        default=0,
        validators=[Optional(), NumberRange(min=0, message="لا يمكن أن يكون سالباً.")],
    )
    # TC-4.1: allow entering initial stock at creation time
    initial_qty = DecimalField(
        "الرصيد الابتدائي (اختياري — للجرد الافتتاحي)",
        places=3,
        default=0,
        validators=[Optional(), NumberRange(min=0, message="لا يمكن أن يكون سالباً.")],
    )
    initial_price = DecimalField(
        "سعر الوحدة الابتدائي (اختياري)",
        places=2,
        default=0,
        validators=[Optional(), NumberRange(min=0)],
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("حفظ")


class StockAdjustForm(FlaskForm):
    delta = DecimalField(
        "كمية التعديل (+/-)",
        places=3,
        validators=[DataRequired(message="الكمية مطلوبة.")],
    )
    reason = StringField(
        "سبب التعديل",
        validators=[DataRequired(message="السبب مطلوب."), Length(max=255)],
    )
    submit = SubmitField("تسجيل التعديل")
