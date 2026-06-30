"""
auth.py — تسجيل دخول مالك البزنس للداشبورد (إيميل + باسورد)

الاستخدام:
    from auth import auth_bp, login_manager, login_required_dashboard
    login_manager.init_app(app)
    app.register_blueprint(auth_bp)

    @app.route("/dashboard/products")
    @login_required_dashboard
    def products():
        ...
"""

from functools import wraps
from flask import Blueprint, request, redirect, url_for, session, render_template, flash
from flask_login import (
    LoginManager, login_user, logout_user, current_user, login_required
)

from models import User, Tenant

auth_bp = Blueprint("auth", __name__)
login_manager = LoginManager()
login_manager.login_view = "auth.login"


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(user_id)


def login_required_dashboard(f):
    """نفس login_required العادي، لكن بيتأكد كمان إن الـ tenant بتاعه لسه active"""
    @wraps(f)
    @login_required
    def wrapper(*args, **kwargs):
        tenant = Tenant.query.get(current_user.tenant_id)
        if not tenant or not tenant.is_active:
            logout_user()
            flash("حسابك غير مفعّل حالياً، تواصل مع الدعم.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return wrapper


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            login_user(user, remember=True)
            return redirect(url_for("dashboard.home"))

        flash("الإيميل أو الباسورد غلط", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
