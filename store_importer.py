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
def _fetch_easyorders_page(api_key, page, limit=100):
    """
    يجيب صفحة واحدة من منتجات EasyOrders — بيرجع (list, error)

    مهم: endpoint القائمة بيرجّع حقول مختصرة افتراضياً (price بس، من غير
    sale_price ولا description). لازم نطلب الحقول صراحةً بـ fields=
    (مؤكد من التشخيص في الإنتاج: keys=['price'] فقط بدون fields)
    """
    endpoint = "https://api.easy-orders.net/api/v1/external-apps/products"
    wanted_fields = ("id,name,price,sale_price,description,slug,sku,"
                     "thumb,images,is_free_shipping,quantity")

    def _do_request(with_fields):
        params = {"page": page, "limit": limit}
        if with_fields:
            params["fields"] = wanted_fields
        return requests.get(
            endpoint,
            params=params,
            headers={"Api-Key": api_key, "Content-Type": "application/json"},
            timeout=25,
        )

    try:
        resp = _do_request(with_fields=True)
        # لو الـ fields param مش مدعوم ورجّع خطأ → جرّب من غيره
        if resp.status_code == 400:
            resp = _do_request(with_fields=False)
    except Exception as e:
        return None, f"تعذّر الاتصال بـ EasyOrders: {str(e)[:80]}"

    if resp.status_code in (401, 403):
        return None, "مفتاح الـ API غير صحيح أو مالوش صلاحية قراءة المنتجات (products:read)"
    if resp.status_code != 200:
        return None, f"EasyOrders رجّع خطأ (كود {resp.status_code})"

    try:
        data = resp.json()
    except Exception:
        return None, "رد EasyOrders مش صالح"

    # الرد ممكن يكون list مباشرة أو object فيه data/products
    raw = data if isinstance(data, list) else (
        data.get("data") or data.get("products") or []
    )
    return raw, None


def _parse_eo_price(value):
    """يحوّل السعر لرقم بأمان (بيتعامل مع string و None و 0)"""
    if value is None:
        return None
    try:
        num = float(value)
        return num if num > 0 else None
    except (ValueError, TypeError):
        return None


def _extract_eo_prices(p):
    """
    استخراج (سعر التخفيض، السعر الأصلي) من منتج EasyOrders بشكل شامل.
    بيجرّب: الحقول المباشرة بكل التسميات المحتملة → الـ variants كـ fallback.
    البيانات الحقيقية أحياناً بتختلف عن التوثيق (sale_price بيكون null
    على مستوى المنتج وموجود في الـ variants).
    """
    # 1) الحقول المباشرة (بكل التسميات المحتملة)
    sale = (_parse_eo_price(p.get("sale_price"))
            or _parse_eo_price(p.get("salePrice"))
            or _parse_eo_price(p.get("discount_price"))
            or _parse_eo_price(p.get("offer_price")))
    original = (_parse_eo_price(p.get("price"))
                or _parse_eo_price(p.get("original_price"))
                or _parse_eo_price(p.get("regular_price")))

    # 2) fallback: الـ variants (المنتجات متعددة الخيارات بتخزن السعر هناك)
    if not sale or not original:
        variants = p.get("variants") or []
        v_sales = [x for x in (_parse_eo_price(v.get("sale_price")) for v in variants if isinstance(v, dict)) if x]
        v_prices = [x for x in (_parse_eo_price(v.get("price")) for v in variants if isinstance(v, dict)) if x]
        if not sale and v_sales:
            sale = min(v_sales)
        if not original and v_prices:
            original = min(v_prices)

    # 3) تنظيف منطقي: التخفيض لازم يكون أقل من الأصلي
    if sale and original and sale >= original:
        # مفيش تخفيض حقيقي — الـ sale هو السعر الفعلي
        return None, sale
    return sale, original


def import_from_easyorders_api(api_key, max_products=500):
    """
    استيراد المنتجات من EasyOrders عبر الـ API الرسمي (بالـ API Key).
    ده الحل المضمون لمتاجر EasyOrders — بيجيب كل المنتجات بدقة.

    - بيلف على كل الصفحات (pagination) عشان يجيب كل المنتجات مش أول 20 بس
    - بيفضّل sale_price (سعر التخفيض) لأنه السعر الفعلي للبيع
    - بيجيب الوصف كامل عشان الـ onboarding يبقى جاهز

    التاجر بياخد الـ API Key من:
    حساب EasyOrders → Public API → Create New API Key (بصلاحية products:read)

    بيرجع dict: {"products": [...], "error": ...}
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return {"products": [], "error": "مفتاح الـ API فارغ"}

    # ── Pagination: نلف على كل الصفحات لحد ما المنتجات تخلص ──
    all_raw = []
    page = 1
    per_page = 100
    prev_first_id = None
    while len(all_raw) < max_products:
        raw, err = _fetch_easyorders_page(api_key, page, per_page)
        if err:
            # لو أول صفحة فشلت → خطأ حقيقي. لو صفحة لاحقة → نكتفي باللي جمعناه
            if page == 1:
                return {"products": [], "error": err}
            break
        if not raw:
            break   # صفحة فاضية = مفيش منتجات تانية

        # حماية من الـ APIs اللي بتتجاهل page وبترجّع نفس النتائج كل مرة
        first_id = raw[0].get("id") or raw[0].get("slug") or raw[0].get("name")
        if first_id and first_id == prev_first_id:
            break   # نفس الصفحة اتكررت — نقف عشان مانلفش للأبد
        prev_first_id = first_id

        all_raw.extend(raw)
        page += 1
        if page > 50:
            break   # حد أمان أقصى (50 صفحة)

    if not all_raw:
        return {"products": [], "error": "مفيش منتجات في المتجر"}

    # ── تشخيص: نطبع حقول أول منتج في اللوج ──
    # لو السعر/الوصف طلع ناقص، اللوج ده هيوضح شكل البيانات الحقيقية فوراً
    first = all_raw[0]
    print(f"🔍 EasyOrders sample [{first.get('name','?')[:30]}]: "
          f"price={first.get('price')!r} sale_price={first.get('sale_price')!r} "
          f"desc_len={len(first.get('description') or '')} "
          f"all_keys={sorted(first.keys())}")

    products = []
    for p in all_raw[:max_products]:
        name = (p.get("name") or "").strip()
        if not name:
            continue

        # ── السعر: استخراج شامل (حقول مباشرة + variants) مع تفضيل التخفيض ──
        sale, original = _extract_eo_prices(p)
        price = sale if sale else original

        # ── الوصف: كامل (مش مقطوع) عشان البوت يستخدمه في البيع ──
        desc = re.sub(r"<iframe[^>]*>.*?</iframe>", " ", p.get("description", "") or "", flags=re.DOTALL)
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()[:1000]

        # الصور
        images = []
        if p.get("thumb"):
            images.append(p["thumb"])
        for img in (p.get("images") or []):
            if isinstance(img, str) and img:
                images.append(img)

        # الشحن المجاني
        is_free = p.get("is_free_shipping", False)
        price_note = ""
        if price:
            price_note = f"{price:.0f} ج" + (" شامل الشحن" if is_free else "")
            # لو فيه تخفيض فعلي، نبرزه (سلاح بيع قوي)
            if sale and original and sale < original:
                price_note = f"{sale:.0f} ج بدل {original:.0f} ج" + (" — شامل الشحن" if is_free else "")

        slug = p.get("slug", "")
        products.append({
            "name": name,
            "description": desc,
            "keywords": _make_keywords(name, ""),
            "price_amount": price,
            "price_original": original if (sale and original and sale < original) else None,
            "price_note": price_note,
            "features": "",
            "product_link": "",   # الـ API مابيرجعش رابط الصفحة مباشرة
            "image_urls": ",".join(images[:3]),
            "is_free_shipping": is_free,
            "easyorders_slug": slug,
        })

    return {"products": products, "error": None}


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
