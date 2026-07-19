from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length


class LoginForm(FlaskForm):
    email = StringField(
        "البريد الإلكتروني",
        validators=[DataRequired(message="الإيميل مطلوب."), Email(message="صيغة الإيميل غير صحيحة.")],
    )
    password = PasswordField(
        "كلمة المرور",
        validators=[DataRequired(message="كلمة المرور مطلوبة.")],
    )
    remember_me = BooleanField("تذكرني")
    submit = SubmitField("تسجيل الدخول")


class ForgotPasswordForm(FlaskForm):
    email = StringField(
        "البريد الإلكتروني",
        validators=[DataRequired(message="الإيميل مطلوب."), Email(message="صيغة الإيميل غير صحيحة.")],
    )
    submit = SubmitField("أرسل رابط استرجاع كلمة المرور")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "كلمة المرور الحالية",
        validators=[DataRequired(message="كلمة المرور الحالية مطلوبة.")],
    )
    new_password = PasswordField(
        "كلمة المرور الجديدة",
        validators=[
            DataRequired(message="كلمة المرور الجديدة مطلوبة."),
            Length(min=8, message="كلمة المرور يجب أن تكون 8 أحرف على الأقل."),
        ],
    )
    confirm = PasswordField(
        "تأكيد كلمة المرور",
        validators=[
            DataRequired(message="التأكيد مطلوب."),
            EqualTo("new_password", message="كلمتا المرور غير متطابقتين."),
        ],
    )
    submit = SubmitField("حفظ كلمة المرور الجديدة")


class ResetPasswordForm(FlaskForm):
    password = PasswordField(
        "كلمة المرور الجديدة",
        validators=[
            DataRequired(message="كلمة المرور مطلوبة."),
            Length(min=8, message="كلمة المرور يجب أن تكون 8 أحرف على الأقل."),
        ],
    )
    confirm = PasswordField(
        "تأكيد كلمة المرور",
        validators=[
            DataRequired(message="التأكيد مطلوب."),
            EqualTo("password", message="كلمتا المرور غير متطابقتين."),
        ],
    )
    submit = SubmitField("تحديث كلمة المرور")
