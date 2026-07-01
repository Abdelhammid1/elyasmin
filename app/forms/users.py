from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, Length, Optional

from app.models.auth import User


ROLE_CHOICES = [
    (User.ROLE_ADMIN, "مدير النظام"),
    (User.ROLE_MANAGER, "مدير مزرعة"),
    (User.ROLE_VIEWER, "مشاهد فقط"),
]


class UserCreateForm(FlaskForm):
    email = StringField(
        "البريد الإلكتروني",
        validators=[DataRequired(message="الإيميل مطلوب."), Email(message="صيغة الإيميل غير صحيحة.")],
    )
    full_name = StringField(
        "الاسم الكامل",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    role = SelectField("الصلاحية", choices=ROLE_CHOICES, validators=[DataRequired()])
    password = PasswordField(
        "كلمة المرور المؤقتة",
        validators=[
            DataRequired(message="كلمة المرور مطلوبة."),
            Length(min=8, message="كلمة المرور يجب أن تكون 8 أحرف على الأقل."),
        ],
    )
    is_active = BooleanField("مفعّل", default=True)
    submit = SubmitField("إضافة المستخدم")


class UserEditForm(FlaskForm):
    full_name = StringField(
        "الاسم الكامل",
        validators=[DataRequired(message="الاسم مطلوب."), Length(max=120)],
    )
    role = SelectField("الصلاحية", choices=ROLE_CHOICES, validators=[DataRequired()])
    password = PasswordField(
        "كلمة مرور جديدة (اختياري)",
        validators=[Optional(), Length(min=8, message="كلمة المرور يجب أن تكون 8 أحرف على الأقل.")],
    )
    is_active = BooleanField("مفعّل")
    submit = SubmitField("حفظ التعديلات")
