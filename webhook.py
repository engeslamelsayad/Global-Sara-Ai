"""
webhook.py — استقبال رسائل فيسبوك/انستجرام وتوجيهها للـ tenant الصحيح

التسجيل في app الرئيسي:
    from webhook import webhook_bp
    app.register_blueprint(webhook_bp)
"""

import os
from flask import Blueprint, request, jsonify

from bot_engine import (
    get_tenant_for_page, buffer_message, handle_echo, is_closing_reaction,
    download_meta_image,
)

webhook_bp = Blueprint("webhook", __name__)

VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "mytoken123")


@webhook_bp.route("/webhook", methods=["GET"])
def verify_webhook():
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return challenge, 200
    return "Forbidden", 403


@webhook_bp.route("/webhook", methods=["POST"])
def receive_message():
    data = request.get_json()

    try:
        platform = data.get("object")
        if platform not in ["page", "instagram"]:
            return jsonify({"status": "ok"}), 200

        for entry in data.get("entry", []):
            page_id = str(entry.get("id"))

            bundle = get_tenant_for_page(page_id)
            if not bundle:
                print(f"⚠️ No tenant registered for page {page_id} — ignoring")
                continue

            for event in entry.get("messaging", []):
                if "message" not in event:
                    continue

                # ── رد الموديريتور البشري من الإنبوكس مباشرة ──
                if event["message"].get("is_echo"):
                    handle_echo(bundle, event)
                    continue   # لا تعالج echo كرسالة عادية أبداً

                sender_id    = event["sender"]["id"]
                user_message = event["message"].get("text", "")

                # ── صورة مرفقة ──
                image_b64, image_media_type = None, "image/jpeg"
                attachments = event["message"].get("attachments", [])
                img_attachments = [a for a in attachments if a.get("type") == "image"]
                if img_attachments:
                    img_url = img_attachments[0].get("payload", {}).get("url", "")
                    page = bundle["pages"].get(page_id)
                    if img_url and page and page.access_token:
                        image_b64, image_media_type = download_meta_image(
                            img_url, page.access_token
                        )
                        if not user_message:
                            user_message = "[صورة]"

                if not user_message and not image_b64:
                    continue

                # ── تجاهل إيماءات الإغلاق (👍 لوحدها) ──
                if is_closing_reaction(user_message, bundle):
                    print(f"👍 Closing reaction ignored from {sender_id}")
                    continue

                print(f"💬 [{page_id}/{platform}] {sender_id}: {user_message[:60]} → buffering")

                # ── تجميع الرسائل (debounce بمدة الـ tenant الخاصة) ──
                buffer_message(bundle, sender_id, user_message, page_id, platform,
                               image_b64, image_media_type)

    except Exception as e:
        print(f"❌ Webhook error: {e}")
        import traceback
        traceback.print_exc()

    return jsonify({"status": "ok"}), 200
