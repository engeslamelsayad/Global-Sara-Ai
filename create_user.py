"""
create_user.py — إنشاء يوزر جديد للداشبورد

التشغيل من Railway Shell:
    python create_user.py

أو مع تحديد البيانات مباشرةً:
    EMAIL=test@test.com PASSWORD=MyPass123 TENANT=eecm python create_user.py
"""

import os
from flask import Flask
from db_init import init_db
from models import db, User, Tenant

app = Flask(__name__)
init_db(app)


def create_user():
    email    = os.environ.get("EMAIL") or input("الإيميل: ").strip()
    password = os.environ.get("PASSWORD") or input("الباسورد: ").strip()
    slug     = os.environ.get("TENANT") or input("slug الشركة (مثال: eecm): ").strip()
    role     = os.environ.get("ROLE", "owner")

    with app.app_context():
        tenant = Tenant.query.filter_by(slug=slug).first()
        if not tenant:
            print(f"❌ Tenant '{slug}' مش موجود.")
            print("الـ slugs الموجودة:", [t.slug for t in Tenant.query.all()])
            return

        existing = User.query.filter_by(email=email).first()
        if existing:
            print(f"⚠️  الإيميل '{email}' موجود بالفعل.")
            change = input("تغيير الباسورد؟ (y/n): ").strip().lower()
            if change == "y":
                existing.set_password(password)
                db.session.commit()
                print(f"✅ تم تغيير باسورد {email}")
            return

        user = User(
            tenant_id=tenant.id,
            email=email,
            full_name=input("الاسم الكامل (اختياري): ").strip() if not os.environ.get("EMAIL") else "",
            role=role,
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        print(f"✅ تم إنشاء الحساب: {email} | شركة: {tenant.business_name} | دور: {role}")


if __name__ == "__main__":
    create_user()
