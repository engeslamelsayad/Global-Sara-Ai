"""
create_tenant.py — إنشاء شركة (tenant) جديدة من الصفر مع أول مستخدم

💡 الطريقة الأسهل دلوقتي: صفحة «إدارة المستخدمين» في الداشبورد (/admin/users).
السكربت ده لسه شغال للطوارئ — والاتنين بيستخدموا provisioning.create_tenant.

التشغيل من Railway Shell:
    python create_tenant.py

أو بدون أسئلة:
    SLUG=trendora NAME="Trendora Store" EMAIL=owner@trendora.com PASSWORD=Pass123! python create_tenant.py
"""

import os
from flask import Flask
from db_init import init_db
from models import db
import provisioning

app = Flask(__name__)
init_db(app)


def prompt(label, env_key=None, default=None):
    val = os.environ.get(env_key, "") if env_key else ""
    if val:
        return val
    if default:
        inp = input(f"{label} [{default}]: ").strip()
        return inp or default
    return input(f"{label}: ").strip()


def run():
    with app.app_context():
        print("\n=== إنشاء شركة جديدة ===\n")

        slug          = prompt("slug الشركة (حروف إنجليزي وشرطة فقط، مثال: trendora)", "SLUG")
        business_name = prompt("اسم الشركة", "NAME")
        email         = prompt("إيميل المالك", "EMAIL")
        password      = prompt("الباسورد", "PASSWORD")
        bot_name      = prompt("اسم البوت", "BOT_NAME", default="سارة")
        dialect       = prompt("اللهجة", "DIALECT", default="مصري")

        try:
            tenant, user, password = provisioning.create_tenant(
                slug, business_name, email, password, bot_name=bot_name, dialect=dialect)
            db.session.commit()
        except provisioning.ProvisionError as e:
            db.session.rollback()
            print(f"\n❌ {e}")
            return

        print(f"""
✅ تم إنشاء الشركة بنجاح!
   الاسم: {tenant.business_name}
   Slug:  {tenant.slug}
   Login: {user.email}

الخطوات التالية في الداشبورد:
  1. أضف منتجاتك من صفحة "المنتجات"
  2. اربط صفحة فيسبوك/انستجرام من "الصفحات المربوطة"
  3. اضبط شخصية البوت من "شخصية البوت"
""")


if __name__ == "__main__":
    run()
