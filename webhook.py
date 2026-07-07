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
    download_meta_image, send_message, capture_ad_referral,
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
                # ── referral منفصل (العميل ضغط الإعلان قبل ما يكتب) ──
                if "message" not in event and "referral" in event:
                    capture_ad_referral(bundle, event, event["sender"]["id"])
                    continue

                if "message" not in event:
                    continue

                # ── رد الموديريتور البشري من الإنبوكس مباشرة ──
                if event["message"].get("is_echo"):
                    handle_echo(bundle, event)
                    continue   # لا تعالج echo كرسالة عادية أبداً

                sender_id    = event["sender"]["id"]
                user_message = event["message"].get("text", "")
                attachments  = event["message"].get("attachments", [])

                # ── التقاط إعلان المصدر لو الرسالة جاية من إعلان ──
                if event["message"].get("referral"):
                    capture_ad_referral(bundle, event, sender_id)

                # ── تجاهل الـ stickers والـ likes (إيماءات مش رسائل) ──
                sticker_atts = [a for a in attachments if a.get("type") in ("like_heart", "fallback")]
                has_sticker = any(
                    a.get("type") == "image" and a.get("payload", {}).get("sticker_id")
                    for a in attachments
                )
                is_like = event["message"].get("sticker_id") or has_sticker or sticker_atts
                if is_like and not user_message:
                    print(f"👍 Sticker/like ignored from {sender_id}")
                    continue

                # ── الرسائل الصوتية: تحويل لنص (Whisper) أو رد توجيهي ──
                audio_atts = [a for a in attachments if a.get("type") == "audio"]
                if audio_atts and not user_message:
                    from bot_engine import transcribe_voice
                    audio_url = audio_atts[0].get("payload", {}).get("url", "")
                    page = bundle["pages"].get(page_id)
                    token = page.access_token if page else None
                    transcribed = transcribe_voice(audio_url, token)
                    if transcribed:
                        # التحويل نجح — الرسالة الصوتية بقت نص وبتتعالج عادي
                        user_message = transcribed
                        print(f"🎤 Voice → text from {sender_id}")
                    else:
                        # التحويل مش متاح (مفيش OPENAI_API_KEY) أو فشل — التوجيه القديم
                        print(f"🎤 Voice message from {sender_id} — sending guidance")
                        voice_reply = (
                            "سمعت إنك بعتّ رسالة صوتية 🎤 بس للأسف مش بقدر أسمع الصوت دلوقتي — "
                            "ممكن تكتبلي اللي محتاجه بالكتابة وأنا تحت أمرك على طول 😊"
                        )
                        send_message(bundle, sender_id, voice_reply, page_id, platform)
                        continue

                # ── صورة مرفقة (مش sticker) ──
                image_b64, image_media_type = None, "image/jpeg"
                img_attachments = [a for a in attachments if a.get("type") == "image"
                                   and not a.get("payload", {}).get("sticker_id")]
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
