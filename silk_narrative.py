"""طبقة الترجمة السردية لسِلك — Silk narrative/translation layer (P1).

مواصفة المالك: «المشكلة في الميل الأخير — التوليف والعرض، لا الوكلاء ولا
البيانات». هذه الطبقة تجلس فوق النموذج القانوني (`silk_render.build_view`)
وتحوّل قيم الآلة إلى عربية بشرية:

  - الدرجات المعيارية 0–1 لا تصل وجه المستخدم أبداً — تُترجم لحالة لغوية
    («منافسة مفتوحة — لا مورّد مهيمن») أو تُخفى.
  - المصطلحات الإحصائية والرموز (HHI، CAGR، CONDITIONAL-GO، أسماء الوكلاء)
    تمرّ عبر معجم إلزامي؛ الإنجليزية تبقى لملحق المحلّل فقط.
  - القيمة الغائبة تُعرض «—» هادئة بلا شعار ولا نسبة اكتمال ولا وعظ نزاهة —
    سطر المصدر تحت الرقم الحاضر هو إشارة النزاهة الوحيدة للمستخدم.

عرض صرف: قراءة حقول محسوبة فقط، صفر شبكة، صفر رقم جديد — «لا اختلاق»
يبقى قاعدة هندسية داخلية ولا يُطبع نصاً للمستخدم أبداً.
Pure display over computed fields; adds no numbers, calls no network.
"""
from __future__ import annotations

import re

# ── المعجم — the mandatory glossary ─────────────────────────────────────────

VERDICT_AR: dict[str, str] = {
    "GO": "التوصية بالدخول",
    "CONDITIONAL-GO": "دخول مشروط",
    "NO-GO": "عدم الدخول حالياً",
    "WATCH": "مراقبة السوق",
}

# أسماء الأسواق بالعربية — أسواق سِلك الـ38 + السعودية (المنشأ).
COUNTRY_AR: dict[str, str] = {
    "SAU": "السعودية", "ARE": "الإمارات", "QAT": "قطر", "KWT": "الكويت",
    "OMN": "عُمان", "BHR": "البحرين", "JOR": "الأردن", "LBN": "لبنان",
    "EGY": "مصر", "MAR": "المغرب", "TUN": "تونس", "DZA": "الجزائر",
    "IRQ": "العراق", "TUR": "تركيا", "YEM": "اليمن", "ZAF": "جنوب أفريقيا",
    "NGA": "نيجيريا", "KEN": "كينيا", "ETH": "إثيوبيا", "GHA": "غانا",
    "IND": "الهند", "PAK": "باكستان", "BGD": "بنغلاديش", "IDN": "إندونيسيا",
    "MYS": "ماليزيا", "SGP": "سنغافورة", "THA": "تايلند", "VNM": "فيتنام",
    "CHN": "الصين", "JPN": "اليابان", "KOR": "كوريا الجنوبية",
    "GBR": "بريطانيا", "DEU": "ألمانيا", "FRA": "فرنسا", "ITA": "إيطاليا",
    "ESP": "إسبانيا", "NLD": "هولندا", "USA": "الولايات المتحدة",
    "CAN": "كندا",
}

# أسماء إنجليزية شائعة → عربية (لحقول تحمل الاسم لا الرمز).
_EN_COUNTRY_AR: dict[str, str] = {
    "United Arab Emirates": "الإمارات", "Saudi Arabia": "السعودية",
    "Kuwait": "الكويت", "Qatar": "قطر", "Oman": "عُمان",
    "Bahrain": "البحرين", "China": "الصين", "India": "الهند",
    "Germany": "ألمانيا", "France": "فرنسا", "United Kingdom": "بريطانيا",
    "United States": "الولايات المتحدة", "USA": "الولايات المتحدة",
    "Japan": "اليابان", "New Zealand": "نيوزيلندا", "Turkey": "تركيا",
    "Egypt": "مصر", "Jordan": "الأردن", "Morocco": "المغرب",
    "Indonesia": "إندونيسيا", "Malaysia": "ماليزيا",
    "Singapore": "سنغافورة", "Netherlands": "هولندا", "Spain": "إسبانيا",
    "Italy": "إيطاليا", "Canada": "كندا", "Pakistan": "باكستان",
    "Thailand": "تايلند", "Vietnam": "فيتنام", "Viet Nam": "فيتنام",
    "South Korea": "كوريا الجنوبية", "Rep. of Korea": "كوريا الجنوبية",
    "Iran": "إيران", "Tunisia": "تونس", "Algeria": "الجزائر",
    "Mexico": "المكسيك", "Argentina": "الأرجنتين", "Ukraine": "أوكرانيا",
    "Brazil": "البرازيل", "Australia": "أستراليا",
}

# أسماء الوكلاء/المقاييس الداخلية → وصف عربي — لا اسم صنف كود يصل المستخدم.
INTERNAL_AR: dict[str, str] = {
    "TradeFlowAgent": "بيانات التدفق التجاري",
    "EconomicAgent": "المؤشرات الاقتصادية",
    "CompetitionAgent": "بيانات المنافسة",
    "market_size": "حجم واردات السوق",
    "saudi_position": "الحصة السعودية",
    "demand_capacity": "دخل الفرد",
    "competition": "تركّز الموردين",
    "tam_usd": "إجمالي واردات السوق (TAM)",
    "sam_usd": "السوق القابل للخدمة (SAM)",
    "som_usd": "الحصة القابلة للتحصيل (SOM)",
    "import_growth_pct": "نمو الواردات",
    "import_cagr_pct": "معدل النمو السنوي المركّب",
    "hhi": "تركّز الموردين",
    "top_supplier_share_pct": "حصة المورّد الأكبر",
    "saudi_share_pct": "الحصة السعودية",
    "border_unit_value_usd_kg": "متوسط سعر الوحدة عند الحدود",
    "saudi_border_unit_value_usd_kg": "سعر الوحدة السعودي عند الحدود",
    "margin_at_border_pct": "الهامش عند الحدود",
    "tariff_applied_pct": "التعريفة الجمركية المطبّقة",
    "political_stability_wgi": "الاستقرار السياسي",
    "regulatory_quality_wgi": "الجودة التنظيمية",
    "logistics_lpi": "الأداء اللوجستي",
    "fx_volatility_pct": "تقلب سعر الصرف",
    "supplier_concentration_hhi": "تركّز مصادر التوريد",
    "gdp_per_capita_usd": "دخل الفرد",
    "population": "عدد السكان",
    "requirements_count": "عدد الاشتراطات",
    "entry_requirements_count": "عدد اشتراطات الدخول",
    "eligibility_gate": "بوابة الأهلية الأوروبية",
    "saudi_suppliers": "مرشّحو الموردين السعوديين",
    "target_distributors": "مرشّحو الموزّعين المستهدفين",
    "retail_references": "مراجع أسعار التجزئة",
    "(SEARCH_API_KEY / الشبكة)": "(مفتاح خدمة البحث / الشبكة)",
    "ramadan_seasonality": "موسمية رمضان",
    "muslim_share_pct": "حصة السكان المسلمين",
    "lpi_timeliness": "الالتزام بمواعيد الشحن",
    "lpi_intl_shipments": "جودة الشحن الدولي",
    # ملاحظات الحُرّاس الداخلية (عقود إنجليزية مثبَّتة بالاختبارات في طبقة
    # البيانات) وأسماء مسارات/مفاتيح داخلية — تسريب سباكة إن وصلت العميل
    # حرفياً. الأطول أولاً: الاستبدال حرفي متسلسل والقصير جزء من الطويل.
    ("paid agent outside /deepen — skipped (structural guard, "
     "no call attempted)"):
        "وكيل مدفوع لا يعمل خارج خدمة التعميق المدفوعة — تخطٍّ بنيوي "
        "بلا أي نداء",
    "requires SEARCH_API_KEY (or SERPER_API_KEY)":
        "يتطلب تهيئة مفتاح خدمة البحث (Serper)",
    "تتطلب SEARCH_API_KEY و/أو GOOGLE_MAPS_API_KEY في بيئة الخادم":
        "يتطلب تهيئة مفاتيح البحث/الخرائط في بيئة الخادم",
    "يتطلب SEARCH_API_KEY / GOOGLE_MAPS_API_KEY":
        "يتطلب تهيئة مفاتيح البحث/الخرائط",
    "عبر /deepen": "عبر خدمة التعميق المدفوعة",
    "LocalPriceAgent": "وكيل أسعار التجزئة المدفوع",
    "retail_prices": "أسعار التجزئة",
    "no shopping results": "لا نتائج تسوّق مرصودة",
    "(ThreadPoolExecutor)": "",
    "ThreadPoolExecutor": "المعالجة المتوازية",
}

# مفاتيح حزمة وكلاء البحث الثمانية الحتمية (silk_research.py، row["research"])
# — نفس التسمية المستخدَمة في لوحة العميل (web/index.html AGENT_AR) كي لا
# يختلف اسم نفس الوكيل بين الدردشة السياقية (analysis_context) واللوحة.
# قاموس منفصل عمداً عن INTERNAL_AR: هذه كلمات إنجليزية شائعة جداً بذاتها
# ("supplier"، "risk"، "pricing") — لو دخلت حلقة الاستبدال الشامل في
# humanize_technical_note() (تُطبَّق على أي نص حرّ) لأفسدت ظهورها العادي
# داخل جمل أخرى غير مقصودة (بلاغ تصحيح: "supplier HHI over 3 suppliers"
# في ملاحظة DataPoint خام تحوّلت لخليط "المورّدون HHI over 3 suppliers").
# تُستهلك حصراً عبر مطابقة تامة (internal_ar) على مفتاح معروف، لا استبدال
# نصّي داخل جملة عشوائية.
_AGENT_KEY_AR: dict[str, str] = {
    "competitor": "المنافسة",
    "regulatory": "الاشتراطات",
    "pricing": "التسعير",
    "risk": "المخاطر",
    "consumer_demand": "ثقافة المستهلك",
    "supplier": "المورّدون",
    "logistics": "اللوجستيات",
}

# رموز مؤشرات البنك الدولي → عربية — لا رمز API خام يصل وجه المستخدم.
_WB_INDICATOR_AR: dict[str, str] = {
    "NY.GDP.PCAP.CD": "دخل الفرد",
    "NY.GDP.PCAP.PP.CD": "دخل الفرد (تعادل القوة الشرائية)",
    "SP.POP.TOTL": "عدد السكان",
    "PV.EST": "الاستقرار السياسي",
    "RQ.EST": "الجودة التنظيمية",
    "RL.EST": "سيادة القانون",
    "GE.EST": "فعالية الحكومة",
    "CC.EST": "مكافحة الفساد",
    "VA.EST": "الصوت والمساءلة",
    "LP.LPI.OVRL.XQ": "الأداء اللوجستي",
    "LP.LPI.TIME.XQ": "الالتزام بالمواعيد اللوجستية",
    "LP.LPI.ITRN.XQ": "جودة الشحن الدولي",
    "PA.NUS.FCRF": "سعر الصرف",
}


def _wb_ar(code: str) -> str:
    return _WB_INDICATOR_AR.get(code, code)


# أنماط ملاحظات تقنية خام شائعة (استثناءات بايثون/أخطاء HTTP/قوالب مصادر
# محدَّدة) → عربية مقروءة. مُرتَّبة الأخصّ أولاً؛ عقود الملاحظات في طبقة
# البيانات (silk_data_layer.py، silk_hs_resolver.py، ...) تبقى كما هي —
# هذا تحويل عرض فقط (silk_render._strip_internal_plumbing يستدعيه أيضاً
# عبر humanize_technical_note، فالإصلاح مركزي مرة واحدة).
_TECH_PATTERNS: list[tuple[re.Pattern, object]] = [
    # البنك الدولي
    (re.compile(r"\b([A-Z]{2}(?:\.[A-Z0-9]+){1,3})\s+fetch failed for\s+(\w+):.*"),
     lambda m: f"{_wb_ar(m.group(1))} ({m.group(2)}): تعذّر الجلب — أعد المحاولة"),
    (re.compile(r"\b([A-Z]{2}(?:\.[A-Z0-9]+){1,3}):\s*no value returned for\s+(\w+)"),
     lambda m: f"{_wb_ar(m.group(1))} ({m.group(2)}): لا قيمة منشورة"),
    (re.compile(r"\bno value returned for\s+(\w+)"),
     lambda m: f"لا قيمة منشورة لـ{m.group(1)}"),
    (re.compile(r"\b([A-Z]{2}(?:\.[A-Z0-9]+){1,3})\s+year=(\d{4})"),
     lambda m: f"{_wb_ar(m.group(1))} (سنة {m.group(2)})"),
    (re.compile(r"شكل ردّ غير متوقع من البنك الدولي:\s*\S+"),
     "تعذّر تفسير رد البنك الدولي"),
    (re.compile(r"البنك الدولي أعاد خطأ API:.*"),
     "البنك الدولي أعاد خطأ فني — أعد المحاولة"),
    (re.compile(r"سجلات البنك الدولي ليست قائمة:\s*\S+"),
     "تعذّر تفسير رد البنك الدولي"),
    (re.compile(r"\b([A-Z]{2}(?:\.[A-Z0-9]+){1,3})\b"),
     lambda m: _wb_ar(m.group(1))),
    # مصنّف HS
    (re.compile(r"no HS match for\s+['\"]([^'\"]*)['\"]"),
     lambda m: f"لا تطابق لتصنيف HS للمنتج «{m.group(1)}»"),
    (re.compile(r"weak match for\s+['\"]([^'\"]*)['\"]\s*"
               r"\(best=['\"]([^'\"]*)['\"],\s*score=([\d.]+)\)"),
     lambda m: (f"تطابق ضعيف لتصنيف HS للمنتج «{m.group(1)}» — أقرب نتيجة: "
                f"{m.group(2)} (نسبة {round(float(m.group(3)) * 100)}%)")),
    (re.compile(r"HS seed empty/unavailable"), "قاعدة تصنيف HS غير متاحة حالياً"),
    (re.compile(r"invalid HS code\s+['\"]([^'\"]*)['\"]"),
     lambda m: f"رمز HS غير صالح: {m.group(1)}"),
    # فاو ستات
    (re.compile(r"FAOSTAT unavailable:\s*non-JSON response for ([\w/]+):"
               r".*\(may require auth\)"),
     lambda m: f"فاو ستات: رد غير مقروء لـ{m.group(1)} — قد يتطلب تفعيل مفتاح"),
    (re.compile(r"FAOSTAT unavailable:\s*fetch failed for ([\w/]+):"
               r".*\(may require auth\)"),
     lambda m: f"فاو ستات: تعذّر الجلب لـ{m.group(1)} — قد يتطلب تفعيل مفتاح"),
    (re.compile(r"FAOSTAT unavailable:\s*unknown area for ISO3 '(\w+)' "
               r"\(no mapping\)"),
     lambda m: f"فاو ستات: لا يغطي هذا المصدر {m.group(1)}"),
    (re.compile(r"FAOSTAT unavailable:.*"), "فاو ستات: غير متاح حالياً"),
    # مصادر أخرى بأنماط "fetch failed" إنجليزية عامة
    (re.compile(r"\bGDELT\b.*fetch failed for.*"),
     "تعذّر جلب أخبار المخاطر — أعد المحاولة"),
    (re.compile(r"\bOpenAlex\b.*fetch failed for.*"),
     "تعذّر جلب المراجع البحثية — أعد المحاولة"),
    # بلاغ المالك: «(بلا شبكة)» كانت تُشخِّص **خطأً** كلَّ فشلٍ من Trends —
    # 429 (تجاوز معدّل: أعد المحاولة لاحقاً) و«pytrends غير مثبَّتة» وانقطاعُ
    # الشبكة الحقيقي أفعالٌ مختلفة. السببُ يُحفَظ ويُعرَّب بعدها عبر
    # `_EXC_FAMILY_AR`/`_http_status_ar` (يعملان بعد هذه القائمة).
    (re.compile(r"pytrends unavailable / no network:\s*(.*)"),
     lambda m: (f"بيانات الاتجاهات غير متاحة: {m.group(1)}"
                if m.group(1).strip() else
                "بيانات الاتجاهات غير متاحة (بلا شبكة)")),
    (re.compile(r"Volza:\s*no named importers parsed for HS(\d+) into (\w+)"),
     lambda m: f"فولزا: لا مستوردون بالاسم مرصودون لرمز {m.group(1)} في {m.group(2)}"),
    (re.compile(r"Explee unavailable:.*"), "إكسبلي غير متاح حالياً"),
    (re.compile(r"Google Maps API status=(\w+):.*"),
     lambda m: f"خرائط جوجل: تعذّر الجلب ({m.group(1)})"),
    # فشل نداء كلود — رموز داخلية (تدقيق H4، بلاغ هولندا): failure_reason
    # يحمل "empty_response"/"stop_reason='max_tokens'"/"راجع سجلّات الخادم"
    # التي كانت تنجو من التعقيم وتصل /ask و/report.md وحدود التقرير. تُعرَّب
    # هنا قبل أي سطح عميل — نفس نقطة التعريب المركزية.
    (re.compile(r"\bstop_reason\s*=\s*'?max_tokens'?"),
     "بلغ التوليد الحدّ الأقصى للطول"),
    (re.compile(r"\bstop_reason\s*=\s*'?[\w-]+'?"), ""),   # أي سبب آخر — يُزال الرمز الخام
    # تدقيق v2 (الموجة ١، تسريب المشرف #1): «stop_reason = » بفراغٍ وبلا قيمة
    # (أو أيّ صيغة عارية) كانت تنجو من النمطين أعلاه (يطلبان `=`+قيمة). شبكة
    # أمان تُزيل الرمز العاري مهما تباعدت الفراغات حوله.
    (re.compile(r"\bstop_reason\b\s*=?\s*'?[\w-]*'?"), ""),
    (re.compile(r"\bempty_response\b:?\s*"), ""),          # نوع فشل داخلي
    # تدقيق v2 (تسريب المشرف #5): الصيغة غير المشكَّلة «سجلات الخادم» (بلا شدّة
    # على اللام) كانت تنجو من النمط المشكَّل «سجلّات». الشدّة اختيارية الآن،
    # و«راجع» اختيارية — أيّ توجيهٍ لسجلّات الخادم يُزال (لا يخصّ العميل).
    (re.compile(r"\s*[—–-]?\s*(?:راجع\s*)?سجلّ?ات\s+الخادم"), ""),
    # ملاحظة: عدّ نداءات الأدوات العربي «نداءات أدوات: N» **لا يُجرَّد هنا** —
    # هو تِلِمتري تتبّعٍ يُقرأ من الملخّص الخام قبل العرض (silk_render يجرّده
    # لسطح العرض بعد استخراج التتبّع). تجريده هنا (نقطة مركزية) يصفّر عدّ اللوحة.
]

# ── تعريب أخطاء المصادر — يُترجِم ولا يبتلع ─────────────────────────────────
# بلاغ المالك: «فشل نداء التحليل الآلي (تعذّر الاتصال بالمصدر — خطأ تقني مؤقت»
# — قوسٌ غير مغلق، وكلُّ أنماط الفشل منهارةٌ في سلسلةٍ واحدة. السبب: النمط
# القديم كان ينتهي بـ`.*` فيبتلع بقيةَ السطر — رمزَ الحالة والرسالةَ والقوسَ
# الخاتم معاً. القاعدة الآن: **استبدل الرمزَ التقني وحده، وأبقِ الذيلَ
# التشخيصي**؛ ولكلّ عائلةِ فشلٍ عبارتُها المميِّزة (مهلةُ اتصالٍ ≠ مهلةُ ردٍّ ≠
# تجاوزُ معدّلٍ ≠ ردُّ خطأٍ من الخادم) — التشخيص يبدأ من التمييز.
# مرتَّبةٌ من الأخصّ إلى الأعمّ؛ أوّلُ مطابقةٍ تفوز لكلّ موضع.
_EXC_FAMILY_AR = (
    (re.compile(r"\b\w*ConnectTimeout\w*\b"), "انتهت مهلة الاتصال بالمصدر"),
    (re.compile(r"\b\w*ReadTimeout\w*\b"), "انتهت مهلة انتظار ردّ المصدر"),
    (re.compile(r"\b\w*TooManyRequests\w*\b"), "تجاوز حدّ معدّل الطلبات"),
    (re.compile(r"\b\w*RateLimit\w*\b"), "تجاوز حدّ معدّل الطلبات"),
    (re.compile(r"\b\w*(?:Timeout|Timedout)(?:Error|Exception)?\b"),
     "انتهت مهلة النداء"),
    (re.compile(r"\b\w*(?:SSL|Proxy)\w*(?:Error|Exception)\b"),
     "تعذّر تأمين الاتصال بالمصدر"),
    (re.compile(r"\b\w*Connection\w*(?:Error|Exception)\b"),
     "تعذّر الاتصال بالمصدر"),
    (re.compile(r"\b\w*HTTPError\b"), "ردّ خطأ من المصدر"),
    (re.compile(r"\b\w*JSONDecode\w*(?:Error|Exception)?\b"),
     "ردّ غير مقروء من المصدر"),
    (re.compile(r"\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b"),
     "خطأ تقني من المصدر"),
)
# `[^.؛)]*` لا `[^.؛]*`: الذيلُ لا يعبر قوساً خاتماً، وإلا ابتلع القوسَ نفسه
# فأنتج نفسَ عيبِ القوس المفتوح داخل «(... ConnectionPool(...) ...)».
_CONN_POOL_RE = re.compile(r"\b\w*ConnectionPool\([^)]*\)[^.؛)]*")
_HTTP_STATUS_RE = re.compile(r"\bHTTP\s*(\d{3})\b:?[ \t]*")
# بلاغ المالك (Nadec/اليمن #7): «خطأ (HTTP 429):» كانت تُعرَّب إلى
# «خطأ (خطأ استجابة الخادم (HTTP 429)):» — بادئة خطأ مزدوجة + ترقيم فارغ
# «): » معلّق. يُطوى الغلاف «خطأ (…)» قبل التعريب العام، ويُطوى الشكل
# المزدوج المخزَّن من تشغيلات سابقة عند إعادة عرضه.
_ERR_HTTP_DOUBLED_RE = re.compile(
    r"خطأ\s*\(\s*خطأ استجابة الخادم\s*\(HTTP\s*(\d{3})\)\s*:?\s*\)\s*:?[ \t]*")
_ERR_HTTP_WRAPPED_RE = re.compile(
    r"خطأ\s*\(\s*HTTP\s*(\d{3})\s*\)\s*:?[ \t]*")


def _http_status_ar(m: "re.Match") -> str:
    """رمزُ الحالة يبقى ظاهراً — كان يُمحى فيُخلِّف «خطأ استجابة الخادم: )».

    429 (تجاوز معدّل، أعد المحاولة) و503 (المصدر معطّل) و404 (لا سجلّ) أفعالٌ
    تشغيليةٌ مختلفة تماماً — طمسُها في سلسلةٍ واحدة يُلغي قيمةَ الرسالة. غيرُ
    الأخطاء (2xx/3xx) يمرّ كما هو بلا تعريب.

    idempotent (بلاغ Nadec/اليمن #7): «HTTP nnn» داخل بادئةٍ معرَّبة سابقاً
    («…استجابة الخادم (») يمرّ كما هو — تطبيقُ التعريب مرّتين على نصٍّ
    مخزَّن كان يضاعف البادئة «خطأ (خطأ استجابة الخادم …»."""
    code = m.group(1)
    if m.string[max(0, m.start() - 24):m.start()].endswith("استجابة الخادم ("):
        return m.group(0)
    if code[:1] in ("4", "5"):
        return f"خطأ استجابة الخادم (HTTP {code}): "
    return m.group(0)


# قوسٌ فُتِح ولم يُغلَق (أو العكس) بعد حذف/استبدال نصٍّ تقني — يُنظَّف بنيوياً
# بدل الاعتماد على أن كلَّ نمطٍ «مهذَّب». هذا حارسُ العيب المُبلَّغ حرفياً.
def _balance_parens(s: str) -> str:
    """أسقِط القوسَ **المفتوحَ** غير المُغلَق — العيبُ المُبلَّغ حرفياً.

    قوسٌ فُتِح ولم يُغلَق أثرٌ لاستبدالٍ ابتلع خاتمتَه («(تعذّر الاتصال
    بالمصدر — خطأ تقني مؤقت»)، فيُسقَط. أمّا القوسُ الخاتمُ الشاردُ فيبقى
    كما هو: في النثر العربي هو **علامةُ ترقيمٍ مشروعة** لا خلل — تعدادُ
    «1) 2) 3)» و«أ) ب)» أشيعُ صوره، وإسقاطُه يشوّه المتن (اكتُشف على
    `samples/research_report_latest.md` قبل الدمج: «1)» صارت «1»)."""
    stack: list[int] = []
    for i, ch in enumerate(s):
        if ch == "(":
            stack.append(i)
        elif ch == ")" and stack:
            stack.pop()
    if not stack:
        return s
    drop = set(stack)
    return "".join(c for i, c in enumerate(s) if i not in drop)

# رمز بسيط (حروف/أرقام/شرطة سفلية فقط، مثل "supplier" أو "market_size") —
# يُستبدل بحدود كلمة \b كي لا يخترق كلمة أطول تحتويه حرفياً (بلاغ: مفتاح
# "supplier" الجديد كان يفسد "saudi_suppliers" إلى "saudi_المورّدونs" عبر
# استبدال حرفي أعمى). القوالب الإنجليزية الطويلة (جمل/عبارات حراس) تبقى
# على الاستبدال الحرفي كسابقاً — لا حدود كلمة لها أصلاً لأنها ليست رمزاً واحداً.
_SIMPLE_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
_word_token_cache: dict[str, re.Pattern] = {}


def _replace_token(s: str, token: str, ar: str) -> str:
    if _SIMPLE_TOKEN_RE.match(token):
        rx = _word_token_cache.get(token)
        if rx is None:
            rx = re.compile(r"\b" + re.escape(token) + r"\b")
            _word_token_cache[token] = rx
        return rx.sub(ar, s)
    return s.replace(token, ar)


def humanize_technical_note(text: object) -> str:
    """حوّل ملاحظة تقنية خام (استثناء بايثون/خطأ HTTP/قالب مصدر داخلي)
    لعربية مقروءة — نقطة التعريب المركزية الوحيدة، يستدعيها كل من
    `translate_gaps` (قوائم الفجوات/الحدود) و
    `silk_render._strip_internal_plumbing` (نصوص حرة: ملخّصات
    البعثات/المحلل/الملاحظات غير المحلولة) فلا يتكرر الإصلاح مرتين.

    عقود ملاحظات DataPoint في طبقة البيانات تبقى كما هي حرفياً — هذا
    تحويل عرض فقط، لا تعديل على قيمة أو مصدر أو حقل مخزَّن.
    """
    s = str(text or "")
    if not s:
        return s
    for token, ar in INTERNAL_AR.items():
        s = _replace_token(s, token, ar)
    for en, ar in _EN_COUNTRY_AR.items():
        s = s.replace(en, ar)
    for rx, repl in _TECH_PATTERNS:
        s = rx.sub(repl, s)
    s = re.sub(r"missing \(no [^)]*\)", "غير متوفر", s)
    s = re.sub(r"\bmissing\b", "غير متوفر", s)
    s = re.sub(r"\(no [^)]*signal\)", "", s)
    s = re.sub(r"\bunobserved\b", "غير مرصود", s)
    # الشبكة الأمان الأخيرة: أي اسم صنف استثناء بايثون أو جزء اتصال HTTP
    # خام لم يلتقطه نمط معروف أعلاه — لا نص تقني خام يمر أبداً للعميل.
    # يُترجَم الرمزُ التقني وحده؛ الذيلُ التشخيصي (رمزُ الحالة/الرسالة) يبقى.
    s = _CONN_POOL_RE.sub("تعذّر الاتصال بالمصدر", s)
    for rx, ar in _EXC_FAMILY_AR:
        s = rx.sub(ar, s)
    # اطوِ غلاف «خطأ (…)» حول رمز HTTP قبل التعريب العام — وإلا تضاعفت
    # البادئة «خطأ (خطأ استجابة الخادم (HTTP 429)):» (المزدوج المخزَّن أولاً
    # لأن النمط الأعمّ يبتلع نصفه).
    s = _ERR_HTTP_DOUBLED_RE.sub(r"خطأ استجابة الخادم (HTTP \1): ", s)
    s = _ERR_HTTP_WRAPPED_RE.sub(r"خطأ استجابة الخادم (HTTP \1): ", s)
    s = _HTTP_STATUS_RE.sub(_http_status_ar, s)
    s = _balance_parens(s)
    # قوسٌ صار فارغاً بعد التنظيف («()») لا يحمل معلومة — يُسقَط.
    s = re.sub(r"\(\s*\)", "", s)
    # قوسٌ خاتمٌ شاردٌ في **ذيل** النصّ مسبوقٌ بترقيمٍ فارغ («…: )») حطامُ
    # استبدال، لا ترقيم. مشروطٌ بوجود فائضٍ فعليٍّ من الخواتم كي لا يُمسّ
    # تعدادٌ مشروع («… 3)») ولا قوسٌ متوازنٌ ينتهي به السطر.
    if s.count(")") > s.count("("):
        s = re.sub(r"[\s:—]+\)\s*$", "", s)
    # مسافةٌ قبل قوسٍ لاصقٍ لكلمةٍ عربية (نتجت عن استبدال رمزٍ لاتينيٍّ كان
    # ملاصقاً لقوسه، مثل `TooManyRequestsError(...)`) — قراءةٌ لا معنى.
    s = re.sub(r"([ء-ي])\(", r"\1 (", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip(" —:")


GAP = "—"          # القيمة الغائبة: شرطة هادئة، لا شعار ولا شرح مكرّر.
GAP_WORD = "غير متوفر"


def country_ar(code_or_name: object, fallback: str | None = None) -> str:
    """اسم السوق بالعربية — من ISO3 أو الاسم الإنجليزي؛ يسقط للأصل بلا تخمين."""
    s = str(code_or_name or "").strip()
    return (COUNTRY_AR.get(s.upper()) or _EN_COUNTRY_AR.get(s)
            or fallback or s or GAP)


def authoritative_verdict(verdict: "dict | None") -> "tuple[object, object]":
    """(الحكم المعروض، ثقته) من قاموس التوليف — WP-1 (برنامج إصلاح جودة
    التقارير): **الحكم الحتمي أولاً**. المرحلة الحتمية
    (`verdict["verdict"]`/`["confidence"]` — لجنة التحكيم/المحرّك الموزون)
    هي المصدر الوحيد للحكم المعروض على أي سطح؛ قراءة كلود (`ai.verdict`)
    حقل استشاري داخلي («قراءة تحليلية للذكاء الاصطناعي») لا يُعرَض توصيةً
    أبداً — يُستعمَل احتياطاً فقط حين يغيب الحكم الحتمي كلياً (مدوّنات
    قديمة خُزِّنت بلا مرحلة حتمية). كان الترتيب معكوساً (ai أولاً) فأنتج
    تشغيلتان بنفس المدخلات حكمين مختلفين (WATCH ثم GO) في يومٍ واحد."""
    v = verdict if isinstance(verdict, dict) else {}
    ai = v.get("ai") if isinstance(v.get("ai"), dict) else {}
    raw = v.get("verdict") or ai.get("verdict") or ""
    conf = v.get("confidence")
    if conf is None:
        conf = ai.get("confidence")
    return raw, conf


def verdict_ar(verdict: object) -> str:
    """الحكم بالعربية — رمز الآلة (GO/CONDITIONAL-GO/NO-GO) لا يصل المستخدم."""
    s = str(verdict or "").strip().upper()
    if not s:
        return "تعذّر إصدار توصية"
    if "INSUFFICIENT" in s:
        return "تعذّر إصدار توصية — بيانات غير كافية"
    # حكمُ اللجنة الحتمية حين تنقص التغطية (وكيل/بعثة فشلت) مع بقاء نتائج
    # حقيقية — حكمٌ صادر، لا غياب. كان يسقط لآخر الدالة فيُعاد خاماً/unknown.
    if "INCONCLUSIVE" in s:
        return "نتيجة مبدئية — غير محسومة"
    if "PRELIMINARY" in s and "NO-GO" not in s and "GO" in s:
        return "توصية أولية بالدخول"
    for key in ("CONDITIONAL-GO", "NO-GO", "GO", "WATCH"):
        if key in s:
            return VERDICT_AR[key]
    # مراجعة الشيفرة (عائلة اللائحتين ٤٤/٤٥): بعض مسارات الحكم تضع التسمية
    # العربية مباشرةً بدل الرمز الإنجليزي (`verdict = "دخول مشروط"` لا
    # "CONDITIONAL-GO") — كانت تمرّ من هنا بلا ترجمة (النص الخام كما ورد).
    # نفس التصنيف الواحد المُستعمَل للشارة (silk_render._verdict_tone) بدل
    # مسارٍ ثانٍ غير مترجَم قد يتباعد عنه.
    from silk_render import _verdict_tone
    _tone_key = {"go": "GO", "conditional": "CONDITIONAL-GO",
                "nogo": "NO-GO", "watch": "WATCH"}.get(_verdict_tone(verdict))
    if _tone_key:
        return VERDICT_AR[_tone_key]
    return str(verdict)


def internal_ar(token: object) -> str:
    """مصطلح داخلي (وكيل/مقياس/رمز مؤشر بنك دولي) → عربي؛ غير المعروف يمرّ
    كما هو. المعاجم منفصلة (لا دمج) كي تبقى أنماط `_TECH_PATTERNS`
    الأدق (مثل "PV.EST year=2022" → "الاستقرار السياسي (سنة 2022)") تعمل
    قبل أي استبدال حرفي مبكر؛ و`_AGENT_KEY_AR` (كلمات إنجليزية شائعة
    كمفاتيح وكلاء) لا تدخل حلقة الاستبدال الشامل في
    `humanize_technical_note` — مطابقة تامة هنا فقط."""
    s = str(token or "")
    return (INTERNAL_AR.get(s) or _WB_INDICATOR_AR.get(s)
            or _AGENT_KEY_AR.get(s) or s)


def fmt_money(v: object) -> str:
    """مبلغ بالدولار مقروء — 48.5 مليون دولار / 789 ألف دولار؛ الغائب «—»."""
    if v is None:
        return GAP
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(n) >= 1e9:
        return f"{n / 1e9:.1f} مليار دولار"
    if abs(n) >= 1e6:
        return f"{n / 1e6:.1f} مليون دولار"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.0f} ألف دولار"
    return f"{n:,.0f} دولار"


def fmt_pct(v: object, signed: bool = False) -> str:
    if v is None:
        return GAP
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    sign = "+" if (signed and n > 0) else ""
    return f"{sign}{n:g}%"


def confidence_phrase(c: object) -> str:
    """الثقة كحالة لغوية بنسبة مقروءة — لا كسر عشري خام على وجه التقرير."""
    if c is None:
        return "غير محسوبة"
    try:
        n = float(c)
    except (TypeError, ValueError):
        return str(c)
    pct = round(n * 100)
    # §F-3 (حزمة الفكس v2.1) + WP-1 §4: نطاقات الثقة الموحّدة — عالية ≥80% /
    # متوسطة 60-79% / منخفضة <60%. العتبات والمشتقّ في سُلَّم المعايرة الواحد
    # (silk_style_contract.confidence_band_label) — لا نسخة محلية قد تتباعد.
    from silk_style_contract import confidence_band_label
    return f"{confidence_band_label(pct)} ({pct}%)"


# عتبات شارة الأدلة — ثابت واحد (P0-B، الموجة ٩): بلاغ حي "درجات ثقة تبدو
# بلا سند" — أرقام "(ثقة 0.6)" خام كانت تتخلّل السرد بلا سياق لقارئ غير
# تقني. رقمها الكامل ينتقل لملحق تقني للمدقّقين؛ متن التقرير يحمل شارة
# مبسّطة ثلاثية فقط. رُحِّلت إلى هنا (P2) لتُستعمل في نموذج العرض نفسه
# (silk_render._deep_research_view) لا في طبقة العرض النصي وحدها.
EVIDENCE_VERIFIED_MIN = 0.8
EVIDENCE_SECONDARY_MIN = 0.5


def evidence_badge(confidence: object) -> str:
    """شارة أدلة ثلاثية — ✓ موثّق (مصدر رسمي)/◐ ثانوي (مصدر واحد غير رسمي)/
    ○ غير متحقق (مرشّح غير مؤكَّد) — بدل رقم ثقة خام في متن السرد."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return "○ غير متحقق"
    if c >= EVIDENCE_VERIFIED_MIN:
        return "✓ موثّق"
    if c >= EVIDENCE_SECONDARY_MIN:
        return "◐ ثانوي"
    return "○ غير متحقق"


# WP-3 §1 — وسم المنشأ «(Claude tool-use)» يبقى داخلياً على DataPoint
# ويُجرَّد للعرض فقط؛ هنا يُقرأ لسقف تصنيف الشارة، لا للعرض.
TOOLUSE_MARK_RE = re.compile(r"\(\s*(?:Claude\s*)?tool[-\s]?use\s*\)", re.I)

# وسم سجل الأدلة لبندٍ رفضته المصالحة الرقمية (WP-3 §2).
RECONCILED_OUT_TAG = "متعارض — مستبعد"


def is_agent_gathered(source: object) -> bool:
    """هل جُمِع البند بوكيل بحث (وسم tool-use في مصدره الخام)؟"""
    return bool(TOOLUSE_MARK_RE.search(str(source or "")))


def evidence_badge_for(finding: object) -> str:
    """WP-3 — شارة الأدلة الواعية بالمنشأ والمصالحة، لسجلّ الأدلة وكل عدّاد:

    1. بند أسقطته المصالحة الرقمية (`evidence_tag` = «متعارض — مستبعد»)
       يعرض وسمه بدل أي شارة — لا «✓ موثّق» لرقمٍ رفضه السرد/المصالحة.
    2. بند مُعاد تأطيره «مؤشر سياقي» (رمز HS غير مؤكَّد) يُسقَف عند ◐.
    3. بند جمعه وكيل بحث (وسم tool-use) يُسقَف درجةً واحدة تحت شارة مصدره
       المسمّى (✓→◐، ◐→○) ما لم يُسانده رصدٌ مباشر من جامعٍ رسمي
       (`corroborated=True` يضبطها ممرّ المصالحة في نموذج العرض).
    القيم لا تتغيّر أبداً — التصنيف فقط (عقد عدم الاختلاق)."""
    if isinstance(finding, dict):
        f = finding
    else:
        f = {"confidence": getattr(finding, "confidence", None),
             "source": getattr(finding, "source", None)}
    tag = str(f.get("evidence_tag") or "")
    if tag.startswith(RECONCILED_OUT_TAG):
        return RECONCILED_OUT_TAG
    badge = evidence_badge(f.get("confidence"))
    if tag.startswith("مؤشر سياقي") and badge.startswith("✓"):
        badge = "◐ ثانوي"
    if is_agent_gathered(f.get("source")) and not f.get("corroborated"):
        if badge.startswith("✓"):
            return "◐ ثانوي"
        if badge.startswith("◐"):
            return "○ غير متحقق"
    return badge


def competition_phrase(hhi: object, top_share_pct: object = None,
                       n_suppliers: object = None) -> str:
    """حالة المنافسة بالعربية — مؤشر HHI الخام لا يصل المستخدم أبداً.

    العتبات هي أشرطة وزارة العدل الأمريكية القياسية للتركّز (0.15/0.25) —
    تصنيف معياري معلن فوق رقم مرصود، لا حكم جديد.
    """
    if hhi is None:
        return GAP_WORD
    try:
        h = float(hhi)
    except (TypeError, ValueError):
        return str(hhi)
    if h < 0.15:
        state = "منافسة مفتوحة — لا مورّد مهيمن"
    elif h < 0.25:
        state = "منافسة معتدلة التركّز"
    else:
        state = "سوق عالي التركّز — مورّد أو اثنان يهيمنان"
    bits = [state]
    if n_suppliers:
        bits.append(f"{n_suppliers} مورّداً نشطاً")
    if top_share_pct is not None:
        bits.append(f"حصة الأكبر {fmt_pct(top_share_pct)}")
    return "؛ ".join(bits)


def growth_phrase(cagr_pct: object, growth_pct: object = None,
                  years: str = "") -> str:
    """النمو بالعربية — «CAGR» يُستبدل بمصطلحه العربي الكامل."""
    if cagr_pct is None and growth_pct is None:
        return GAP_WORD
    bits = []
    if growth_pct is not None:
        arrow = "نمو" if float(growth_pct) >= 0 else "انكماش"
        bits.append(f"{arrow} إجمالي {fmt_pct(abs(float(growth_pct)))}"
                    + (f" عبر {years}" if years else ""))
    if cagr_pct is not None:
        bits.append(f"بمعدل نمو سنوي مركّب {fmt_pct(cagr_pct)}")
    return " ".join(bits)


def translate_gaps(gaps: list) -> list[str]:
    """فجوات داخلية (بأسماء وكلاء/مقاييس وهياكل إنجليزية) → عربية كاملة.

    لا اسم صنف كود ولا هيكل جملة إنجليزي يمرّ — ملاحظات الفجوة الداخلية
    تبقى كما هي في نموذج البيانات؛ الترجمة للعرض فقط. مُفوَّض بالكامل
    لـ `humanize_technical_note` — نقطة تعريب واحدة (راجع تعليقها).
    """
    return [humanize_technical_note(g) for g in (gaps or [])]


# ── الخلاصة التنفيذية السردية — the 3-paragraph executive summary ───────────

def market_component_lines(market: dict) -> list[str]:
    """جمل تجارية سردية من مكوّنات سوق واحد أياً كان ترتيبه في القائمة —
    بلا ثقة خام ولا اسم مقياس داخلي؛ سطر واحد لكل حقيقة مرصودة + مصدرها.
    تعذّر الجلب يُذكر صراحة بدل إسقاطه صامتاً — لا فجوة صامتة."""
    drivers: list[str] = []
    comps = {c.get("name"): c for c in
             (market.get("components_detail") or [])}
    ms = comps.get("market_size") or {}
    if ms.get("value") is not None:
        drivers.append(f"يستورد السوق ما قيمته {fmt_money(ms['value'])} سنوياً "
                       f"من هذا المنتج (المصدر: {ms.get('source')})")
    elif ms.get("status") == "fetch_failed":
        drivers.append("حجم واردات السوق: تعذّر الجلب — أعد المحاولة")
    tr = market.get("trend") or {}
    if tr.get("growth_pct") is not None:
        drivers.append(
            growth_phrase(tr.get("cagr_pct"), tr.get("growth_pct"),
                          years="سنوات الدراسة")
            + f" (المصدر: {tr.get('source') or 'UN Comtrade'})")
    sp = comps.get("saudi_position") or {}
    if sp.get("value") is not None:
        drivers.append(f"المنتجات السعودية حاضرة فعلاً بحصة "
                       f"{fmt_pct(sp['value'])} من واردات السوق "
                       f"(المصدر: {sp.get('source')})")
    elif sp.get("status") == "fetch_failed":
        drivers.append("الحصة السعودية: تعذّر الجلب — أعد المحاولة")
    comp = comps.get("competition") or {}
    if comp.get("value") is not None:
        drivers.append(competition_phrase(comp["value"]))
    return drivers


def exec_summary(view: dict) -> list[str]:
    """ثلاث فقرات عربية كاملة: التوصية / لماذا تجارياً / ما ينقص للتأكد.

    بلا درجات معيارية، بلا أسماء وكلاء، بلا شروط كود — كل جملة من حقل محسوب.
    """
    d = view.get("decision") or {}
    top = (view.get("markets") or [{}])[0]
    market = country_ar(top.get("iso3"), d.get("market") or top.get("country"))

    # (أ) التوصية بكلمات بسيطة.
    p1 = f"التوصية: {verdict_ar(d.get('verdict'))} لسوق {market}"
    p1 += f"، بدرجة ثقة {confidence_phrase(d.get('confidence'))}."
    if str(d.get("verdict") or "").upper().find("CONDITIONAL") >= 0:
        p1 += (" الفرصة قائمة، لكن اكتمال الصورة يتطلب استيفاء الشروط "
               "الواردة أدناه قبل قرار نهائي.")

    # (ب) لماذا — تجارياً، من الأرقام المرصودة فقط.
    drivers = market_component_lines(top)
    p2 = ("الأساس التجاري: " + "؛ و".join(drivers) + "."
          if drivers else
          "الأساس التجاري: لم تُرصد بيانات كافية لسوق المنتج بعد — "
          "التوصية مبنية على التغطية الجزئية المتاحة.")

    # (ج) ما ينقص للتأكد — من limits/الشروط، مترجَماً.
    missing = translate_gaps((view.get("limits") or [])[:2])
    ed = top.get("entry_decision") or {}
    if not missing and ed.get("conditions"):
        missing = translate_gaps(ed["conditions"][:2])
    p3 = ("لاكتمال الثقة في هذه التوصية يلزم: " + "؛ ".join(missing) + "."
          if missing else
          "اكتملت مكوّنات التقييم الرئيسية لهذا السوق — لا نواقص جوهرية "
          "تحول دون اعتماد التوصية بوصفها قراءة أولية.")
    return [p1, p2, p3]


def brief_lines(view: dict) -> list[str]:
    """سطور المختصر البشرية — بديل شعارات «القرار: X (ثقة 0.52)» الآلية."""
    d = view.get("decision") or {}
    top = (view.get("markets") or [{}])[0]
    market = country_ar(top.get("iso3"), d.get("market") or top.get("country"))
    lines = [f"{verdict_ar(d.get('verdict'))} — سوق {market} "
             f"(ثقة {confidence_phrase(d.get('confidence'))})"]
    for c in (top.get("components_detail") or []):
        if c.get("value") is None:
            continue
        label = internal_ar(c.get("name"))
        val = (fmt_money(c["value"]) if c.get("name") == "market_size"
               else fmt_pct(c["value"]) if c.get("name") == "saudi_position"
               else competition_phrase(c["value"])
               if c.get("name") == "competition"
               else fmt_money(c["value"]))
        lines.append(f"{label}: {val} — المصدر: {c.get('source')}")
        if len(lines) >= 4:
            break
    return lines
