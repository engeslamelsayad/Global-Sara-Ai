/**
 * {{ business_name }} — Google Apps Script لاستقبال طلبات بوت سارة في Google Sheet
 * (الملف ده اتجهّز من داشبورد منصة سارة — صفحة «بيانات البزنس»)
 *
 * ترتيب الأعمدة في الشيت:
 *   A: التاريخ | B: الاسم | C: الموبايل | D: العنوان | E: المنتج | F: الصفحة | G: الخصم | H: السعر
 *
 * ─────────────── طريقة التركيب (مرة واحدة — 3 دقايق) ───────────────
 *  1) افتح Google Sheet جديد (أو الشيت اللي عايز الطلبات تنزل فيه).
 *  2) من القايمة: Extensions (الإضافات) ← Apps Script.
 *  3) امسح أي كود موجود، والزق الملف ده كله، ودوس 💾 Save.
 *  4) (اختياري) جرّب: اختار testAppend من فوق ودوس Run — هيطلب صلاحيات، وافق عليها.
 *     المفروض يظهر صف تجريبي في الشيت. امسحه بعد كده.
 *  5) Deploy (نشر) ← New deployment (نشر جديد) ← اختار النوع ⚙️ Web app، وبعدين:
 *       - Execute as (التنفيذ باسم): Me (أنا)
 *       - Who has access (مين يقدر يوصل): Anyone (أي شخص)
 *     ودوس Deploy ووافق على الصلاحيات.
 *  6) انسخ الـ Web app URL (بيبدأ بـ https://script.google.com/macros/s/...)
 *     والزقه في خانة «رابط Google Sheet للطلبات» في الداشبورد، ودوس «حفظ التعديلات».
 *
 *  ⚠️ لو عدّلت الكود بعد كده: Deploy ← Manage deployments ← ✏️ ← Version: New version.
 *     (النشر الجديد بيطلّع رابط جديد — لازم تحدّثه في الداشبورد.)
 * ──────────────────────────────────────────────────────────────────
 */

function doPost(e) {
  try {
    // البيانات اللي البوت بيبعتها مع كل طلب (JSON)
    var data = JSON.parse(e.postData.contents);

    // أول شيت في الملف
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getSheets()[0];

    // لو الشيت فاضي، نضيف صف العناوين مرة واحدة
    if (sheet.getLastRow() === 0) {
      sheet.appendRow([
        "التاريخ", "الاسم", "الموبايل", "العنوان",
        "المنتج", "الصفحة", "الخصم", "السعر"
      ]);
    }

    // التوقيت بتوقيت الشيت نفسه (File ← Settings ← Time zone) — بيشتغل في أي بلد
    var now = Utilities.formatDate(new Date(), ss.getSpreadsheetTimeZone(), "yyyy-MM-dd HH:mm:ss");

    sheet.appendRow([
      now,                     // A: التاريخ
      data.name     || "",     // B: الاسم
      data.phone    || "",     // C: الموبايل
      data.address  || "",     // D: العنوان
      data.product  || "",     // E: المنتج
      data.page     || "",     // F: الصفحة (رقم صفحة فيسبوك/إنستجرام)
      data.discount || "",     // G: الخصم
      data.price    || ""      // H: السعر
    ]);

    return ContentService
      .createTextOutput(JSON.stringify({ status: "success" }))
      .setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    // أي خطأ بيرجع للبوت ويظهر في الـ logs
    return ContentService
      .createTextOutput(JSON.stringify({ status: "error", message: err.toString() }))
      .setMimeType(ContentService.MimeType.JSON);
  }
}

/**
 * دالة اختبار — شغّلها مرة واحدة من المحرر للتأكد إن كله تمام (Run → testAppend)
 * المفروض تكتب صف تجريبي في الشيت وتطبع {"status":"success"} في الـ Execution log
 */
function testAppend() {
  var fake = {
    postData: {
      contents: JSON.stringify({
        name: "عميل تجريبي",
        phone: "0000000000",
        address: "عنوان تجريبي",
        product: {{ sample_product }},
        page: "اختبار",
        discount: "",
        price: {{ sample_price }},
        business: {{ business_js }}
      })
    }
  };
  var result = doPost(fake);
  Logger.log(result.getContent());
}
