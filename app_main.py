"""
app_main.py — نقطة دخول التطبيق الرئيسية

يربط: قاعدة البيانات + تسجيل الدخول + الداشبورد + الـ webhook + التحليلات
"""

import os
from flask import Flask, redirect, url_for
from db_init import init_db
from webhook import webhook_bp
from analytics import analytics_bp
from auth import auth_bp, login_manager
from dashboard import dashboard_bp

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-in-production")

init_db(app)

# ── Migrations تلقائية عند كل إقلاع ──────────────────────────────
# آمنة تماماً: كل ALTER بيتخطى لو العمود موجود (idempotent).
# دي بتمنع مشكلة "الكود اترفع قبل الـ migration" اللي كانت بتوقع الداشبورد.
try:
    import migrate_add_columns
    print("🔧 Auto-migrations: فحص أعمدة قاعدة البيانات...")
    migrate_add_columns.run(app)
except Exception as _mig_err:
    # فشل الـ migration مايمنعش التطبيق من الإقلاع — بس نسجّله بوضوح
    print(f"⚠️ Auto-migration error (التطبيق هيكمل): {_mig_err}")

login_manager.init_app(app)

app.register_blueprint(webhook_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)

# صفحة الديمو الحية (عامة — أداة بيع الـ SaaS)
from demo import demo_bp
app.register_blueprint(demo_bp)

# بدء المجدول المركزي (تقارير تليجرام الأسبوعية + معالجة الربط)
from scheduler import start_scheduler
start_scheduler(app)


@app.route("/")
def home():
    return redirect(url_for("dashboard.home"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
