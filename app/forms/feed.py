from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    DateField,
    IntegerField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Length, NumberRange, Optional


class FeedRecipeForm(FlaskForm):
    """Header form for a feed recipe. Line items (ingredient + kg_per_batch) are parsed
    dynamically from request.form."""

    group_id = SelectField("المجموعة", coerce=int, validators=[DataRequired()])
    effective_from = DateField(
        "تاريخ سريان الوصفة", validators=[DataRequired()], default=date.today
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("حفظ الوصفة")


class FeedRunForm(FlaskForm):
    group_id = SelectField("المجموعة", coerce=int, validators=[DataRequired()])
    run_date = DateField("تاريخ التشغيل", validators=[DataRequired()], default=date.today)
    batches_count = IntegerField(
        "عدد الخلطات",
        validators=[
            DataRequired(message="عدد الخلطات مطلوب."),
            NumberRange(min=1, max=200, message="من 1 إلى 200 خلطة."),
        ],
        default=1,
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("تشغيل + خصم من المخزون")


class MedicineDispenseForm(FlaskForm):
    ingredient_id = SelectField("الدواء", coerce=int, validators=[DataRequired()])
    qty = StringField(
        "الكمية",
        validators=[DataRequired(message="الكمية مطلوبة."), Length(max=20)],
    )
    dispense_target = SelectField(
        "الصرف على",
        choices=[("cow", "بقرة معينة"), ("group", "مجموعة كاملة")],
        validators=[DataRequired()],
    )
    cow_id = SelectField("البقرة", coerce=int, validators=[Optional()])
    group_id = SelectField("المجموعة", coerce=int, validators=[Optional()])
    dispensed_on = DateField(
        "تاريخ الصرف", validators=[DataRequired()], default=date.today
    )
    notes = TextAreaField("ملاحظات", validators=[Optional(), Length(max=500)])
    submit = SubmitField("صرف الدواء")
