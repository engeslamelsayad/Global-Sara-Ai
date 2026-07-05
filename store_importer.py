"""
store_importer.py — استيراد المنتجات من رابط المتجر (حل هجين)

الاستراتيجية:
1. يجرّب Shopify API أولاً (/products.json) — دقيق ومنظّم
2. لو مش Shopify، يعمل scraping ذكي: يجيب الصفحة، يستخرج روابط المنتجات،
   ويستخدم الـ AI لاستخراج التفاصيل

كل منتج مستخرج بيرجع بصيغة موحّدة جاهزة للاستيراد في الداتابيز.
"""

import os
import re
import json
import requests
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
EXTRACT_MODEL = "claude-haiku-4-5-20251001"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar,en;q=0.9",
}


# =====================================================================
# نقطة الدخول الرئيسية
# =====================================================================
def import_store(url, dialect="مصري", max_products=30):
    """
    يستورد المنتجات من رابط متجر.
    بيرجع dict: {"method": "shopify"|"easyorders"|"scrape", "products": [...], "error": ...}
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # 1) جرّب Shopify أولاً
    shopify_result = _try_shopify(url, max_products)
    if shopify_result is not None:
        return {"method": "shopify", "products": shopify_result, "error": None}

    # 2) جرّب EasyOrders (منصة مصرية شائعة)
    eo_result = _try_easyorders(url, max_products)
    if eo_result is not None:
        return {"method": "easyorders", "products": eo_result, "error": None}

    # 3) fallback: scraping ذكي
    scrape_result, err = _try_scrape(url, dialect, max_products)
    if scrape_result:
        return {"method": "scrape", "products": scrape_result, "error": None}

    return {"method": None, "products": [], "error": err or "تعذّر استخراج المنتجات من الرابط"}


def _try_easyorders(url, max_products):
    """
    يحاول جلب منتجات EasyOrders عبر الـ storefront API.
    منصة EasyOrders بتحمّل المنتجات بالـ JavaScript من api.easy-orders.net
    بيرجع list لو نجح، None لو مش EasyOrders.
    """
    m = re.match(r"(https?://)([^/]+)", url)
    if not m:
        return None
    domain = m.group(2)

    # EasyOrders بتستخدم الـ full-website-data endpoint للـ storefront
    endpoints = [
        f"https://api.easy-orders.net/api/v1/external-app/full-website-data",
        f"https://{domain}/api/v1/full-website-data",
    ]

    for endpoint in endpoints:
        try:
            headers = dict(HEADERS)
            headers["subdomain"] = domain          # EasyOrders بيحدد المتجر بالـ subdomain header
            headers["Origin"] = f"https://{domain}"
            resp = requests.get(endpoint, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
        except Exception:
            continue

        # نستخرج المنتجات من بنية EasyOrders
        raw_products = data.get("products") or data.get("data", {}).get("products") or []
        if not raw_products:
            continue

        products = []
        for p in raw_products[:max_products]:
            price = p.get("price") or p.get("sale_price")
            try:
                price = float(price) if price else None
            except (ValueError, TypeError):
                price = None

            desc = re.sub(r"<[^>]+>", " ", p.get("description", "") or "")
            desc = re.sub(r"\s+", " ", desc).strip()[:300]

            slug = p.get("slug", "") or p.get("id", "")
            images = []
            if p.get("thumb"):
                images.append(p["thumb"])
            for img in (p.get("images") or []):
                src = img if isinstance(img, str) else img.get("url", "")
                if src:
                    images.append(src)

            name = p.get("name", "").strip()
            if not name:
                continue
            products.append({
                "name": name,
                "description": desc,
                "keywords": _make_keywords(name, ""),
                "price_amount": price,
                "price_note": f"{price:.0f} ج" if price else "",
                "features": "",
                "product_link": f"https://{domain}/products/{slug}" if slug else f"https://{domain}",
                "image_urls": ",".join(images[:3]),
            })

        return products if products else None

    return None


# =====================================================================
# مسار Shopify — /products.json
# =====================================================================
def _try_shopify(url, max_products):
    """
    يحاول جلب منتجات Shopify عبر الـ endpoint القياسي /products.json
    بيرجع list لو نجح، None لو مش Shopify.
    """
    # نجهّز الـ base URL (بدون path)
    m = re.match(r"(https?://[^/]+)", url)
    if not m:
        return None
    base = m.group(1)

    try:
        resp = requests.get(f"{base}/products.json?limit={max_products}",
                            headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if "products" not in data:
            return None
    except Exception:
        return None

    products = []
    for p in data.get("products", [])[:max_products]:
        variants = p.get("variants", [])
        price = None
        if variants:
            try:
                price = float(variants[0].get("price", 0))
            except (ValueError, TypeError):
                price = None

        # الصور
        images = [img.get("src", "") for img in p.get("images", []) if img.get("src")]

        # الوصف (تنظيف HTML)
        body = p.get("body_html", "") or ""
        desc = re.sub(r"<[^>]+>", " ", body)
        desc = re.sub(r"\s+", " ", desc).strip()[:300]

        handle = p.get("handle", "")
        products.append({
            "name": p.get("title", "").strip(),
            "description": desc,
            "keywords": _make_keywords(p.get("title", ""), p.get("tags", "")),
            "price_amount": price,
            "price_note": f"{price:.0f} ج" if price else "",
            "features": "",
            "product_link": f"{base}/products/{handle}" if handle else base,
            "image_urls": ",".join(images[:3]),
        })

    return products if products else None


def _make_keywords(title, tags):
    """يبني كلمات مفتاحية من العنوان والـ tags"""
    words = []
    # كلمات مميزة من العنوان (أطول من حرفين)
    for w in re.split(r"[\s\-–—]+", title):
        w = w.strip()
        if len(w) > 2 and w.lower() not in ("the", "and", "for", "من", "في"):
            words.append(w)
    # tags
    if tags:
        tag_list = tags if isinstance(tags, list) else str(tags).split(",")
        for t in tag_list[:5]:
            t = t.strip()
            if t:
                words.append(t)
    # نشيل التكرار مع الحفاظ على الترتيب
    seen, result = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            result.append(w)
    return ",".join(result[:8])


# =====================================================================
# مسار Scraping — للمواقع غير Shopify
# =====================================================================
def _try_scrape(url, dialect, max_products):
    """
    يجيب صفحة المتجر، يستخرج روابط المنتجات المحتملة،
    ويستخدم الـ AI لتحليل الصفحة واستخراج المنتجات.
    بيرجع (products_list, error)
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        err_str = str(e)
        if "403" in err_str or "Forbidden" in err_str:
            return None, (
                "الموقع ده بيحجب الاستخراج التلقائي 🔒 "
                "الحل: استخدم رابط منتج واحد مباشر، أو أضف المنتجات يدوياً من زر «منتج جديد»."
            )
        return None, f"تعذّر جلب الصفحة: {err_str[:80]}"

    # نستخرج روابط المنتجات المحتملة (patterns شائعة)
    product_links = _extract_product_links(html, url)

    # ننظّف نص الصفحة للـ AI
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:6000]

    # نكتشف لو الصفحة JavaScript-based (منتجات بتتحمّل ديناميكياً)
    loading_count = html.count("Loading") + html.count("جاري التحميل")
    meaningful_len = len(re.sub(r"\s+", "", text))
    if loading_count >= 5 and meaningful_len < 600:
        return None, (
            "الموقع ده بيحمّل منتجاته بطريقة ديناميكية (JavaScript) — صعب نقراها تلقائياً. "
            "الحل: استخدم رابط منتج واحد مباشر (من صفحة المنتج نفسه)، "
            "أو أضف المنتجات يدوياً من زر «منتج جديد»."
        )

    # نستخدم الـ AI لاستخراج المنتجات من النص
    prompt = f"""أنت محلل متاجر إلكترونية. ده محتوى نصي من صفحة متجر:

---
{text}
---

استخرج المنتجات اللي تقدر تلاقيها في الصفحة دي. لكل منتج استخرج اسمه ووصفه وسعره لو موجود.
رد بصيغة JSON فقط (بدون أي نص إضافي):
{{
  "products": [
    {{
      "name": "اسم المنتج",
      "description": "وصف قصير للمنتج لو متاح",
      "price_amount": رقم السعر أو null,
      "keywords": "كلمة1,كلمة2,كلمة3"
    }}
  ]
}}

لو مفيش منتجات واضحة، رد بـ {{"products": []}}. أقصى {max_products} منتج."""

    try:
        resp = client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```json\s*|\s*```$", "", raw).strip()
        parsed = json.loads(raw)
    except Exception as e:
        return None, f"فشل تحليل الصفحة: {str(e)[:80]}"

    products = []
    ai_products = parsed.get("products", [])[:max_products]
    for i, p in enumerate(ai_products):
        name = (p.get("name") or "").strip()
        if not name:
            continue
        price = p.get("price_amount")
        try:
            price = float(price) if price else None
        except (ValueError, TypeError):
            price = None

        # نحاول نطابق رابط منتج
        link = product_links[i] if i < len(product_links) else url

        products.append({
            "name": name,
            "description": (p.get("description") or "").strip()[:300],
            "keywords": p.get("keywords", "") or _make_keywords(name, ""),
            "price_amount": price,
            "price_note": f"{price:.0f} ج" if price else "",
            "features": "",
            "product_link": link,
            "image_urls": "",
        })

    return (products, None) if products else (None, "مالقيناش منتجات واضحة في الصفحة")


def _extract_product_links(html, base_url):
    """يستخرج روابط المنتجات المحتملة من الـ HTML"""
    m = re.match(r"(https?://[^/]+)", base_url)
    domain = m.group(1) if m else ""

    # patterns شائعة لروابط المنتجات
    links = re.findall(r'href=["\']([^"\']*(?:/product[s]?/|/p/|/item/)[^"\']*)["\']', html, re.I)
    full_links = []
    seen = set()
    for link in links:
        if link.startswith("/"):
            link = domain + link
        elif not link.startswith("http"):
            continue
        if link not in seen:
            seen.add(link)
            full_links.append(link)
    return full_links
