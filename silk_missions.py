"""بعثات وكلاء كلود الاثني عشر لسِلك — Silk's 12 Claude tool-use missions
(الموجة ٢ من التكليف الختامي — V5).

كل مهمة: مفتاح + اسم + وصف مهمة + تعليمات مخصّصة (تُلحَق بـ`_PRINCIPLE` في
نظام `silk_llm_runtime._run_loop`) + قائمة أدوات مسموحة حصراً (من
`silk_llm_runtime.TOOLS`). المهمة ١٢ (`opportunity_gaps`) بلا أدوات
خاصة بها — تقرأ نتائج الوكلاء ١-١١ فقط (`extra_findings`، تُسجَّل كنقاط
بيانات قابلة للاستشهاد بها — silk_llm_runtime._run_loop).

التسجيل في `AGENT_CATALOG` **إضافي لا استبدال** (قرار المالك أثناء هذه
الموجة): الصفوف الـ١٤ القائمة تبقى كما هي، وهذه الصفوف الـ١٢ تُضاف
بمفاتيح مختلفة — `/analyze` الحالي لا يتأثر؛ مسار البحث العميق الجديد
(`POST /research`، الموجة ٤) هو من يستدعي `run_all_missions` أدناه.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import (
    FIRST_COMPLETED as cf_FIRST_COMPLETED,
    ThreadPoolExecutor,
    wait as cf_wait,
)

import silk_agents
from silk_agents import AgentReport
from silk_llm_runtime import LLMMissionAgent
from silk_market_resolver import MarketRef

log = logging.getLogger(__name__)

# لغة السوق المستهدف (§ملاحظة التكليف): pricing_scout/consumer_culture/
# channels_importers يُطلَب منها البحث بلغة السوق صراحةً — سطر مشترك يُلحَق
# بتعليماتها الثلاث فلا يتكرر نصاً بحرفه.
_SEARCH_IN_MARKET_LANGUAGE = (
    " ابحث بلغة (لغات) السوق المستهدف، لا الإنجليزية فقط، حين يكون ذلك "
    "متاحاً — نتائج محلية اللغة أدق من ترجمة استعلام إنجليزي.")

# بلاغ حي (الموجة ٩): "التنافسية السعرية"/"ثقافة الاستهلاك"/إلخ كانت تُبنى
# من نداء بحث واحد سطحي — عمق غير كافٍ لتحليل حقيقي. سطر مشترك يُلزم
# البعثات البحثية بأربعة استعلامات مختلفة الزاوية على الأقل قبل الخلاصة.
_MIN_FOUR_SEARCH_ANGLES = (
    " لا تكتفِ بنداء بحث واحد — نفّذ أربعة استعلامات web_search مختلفة "
    "الزاوية على الأقل قبل كتابة الخلاصة (مثال: سعر/توفّر، مستوردون/"
    "موزّعون بالاسم، عادات/سلوك المستهلك، تنظيم/أخبار حديثة) — بلغة "
    "(لغات) السوق حين أمكن، ثم اجمع النتائج معاً بدل عرض أول نتيجة واردة.")

MISSIONS: dict[str, dict] = {
    "pricing_scout": {
        "key": "pricing_scout", "name": "تحليل الأسعار",
        "mission": "المنتجات المنافسة وأسعارها الفعلية في السوق المستهدف",
        "allowed_tools": ["web_search", "trends_interest", "comtrade_imports",
                          "lookup_reference"],
        "instructions": (
            # R1 (القطعة المركزية): ابحث كمستهلك محلي — أولاً اعرف لغة السوق
            # ومتاجره من مرجع locale، ثم ابحث داخلها بلغتها. web_search يطبّق
            # نطاق/لغة السوق تلقائياً، لكن صياغة الاستعلام بلغة السوق ترفع الرصد.
            "الخطوة ١ — استدعِ lookup_reference بالجدول 'locale' (نداء واحد): "
            "يعيد لغة السوق الأساسية ومنصّات البيع المعروفة فيه (سوبرماركت/"
            "متجر إثني-حلال/سوق إلكتروني). ابحث **داخل هذه المنصّات بلغة "
            "السوق** — لا تكتفِ ببحث إنجليزي عام. إن غاب صف locale لهذا السوق، "
            "صرّح بذلك وابحث بلغة السوق قدر ما تستطيع.\n"
            "الخطوة ٢ — لكل منتج منافِس فعلي ترصده، التقط الحقول كاملة: "
            "العلامة/الاسم، بلد المنشأ، حجم العبوة، سعر الرفّ + العملة، "
            "المتجر، الرابط، تاريخ الرصد. طبّع السعر إلى **سعر لكل كجم (أو "
            "وحدة) بالعملة المحلية وبالدولار** (بسعر صرف مُعلَن المصدر) — إن "
            "غاب حجم العبوة فالسعر/كجم فجوة معلنة لا تخمين. وسم كل بند "
            "بدليله: ✓ إن حمل رابط متجر فعلي، ◐ إن كان مُقدَّراً/مشتقاً. أي "
            "سعر بلا رابط = '◐ غير موثَّق'. لا تُقدّر سعراً لم تجده فعلاً؛ "
            "منتج لا سعر له = بند بفجوة معلنة لا حذف. إن لم تجد أي منتج "
            "منافِس، صرّح 'لم تُرصد منتجات منافِسة في هذا السوق' — لا اختلاق.\n"
            "الخطوة ٣ — ابنِ **جدول المنتجات المنافسة** (٣ متاجر/علامات على "
            "الأقل إن توفّرت) لا سعراً واحداً — هذا الجدول هو مخرَجك الأساسي. "
            "استدعِ comtrade_imports أيضاً (نداء واحد يكفي) — إن أعاد "
            "متوسط سعر استيراد (القيمة÷الوزن)، أضفه كسطر أول ثابت في "
            "الجدول موسوماً 'متوسط سعر الاستيراد الرسمي (UN Comtrade، متوسط "
            "عبر مزيج الشحنات)' — نطاق مرجعي واسع لا سعر تجزئة فعلياً ولا "
            "بديلاً عن أسعار المتاجر الحقيقية أعلاه، فقد يخلط درجات جودة "
            "مختلفة داخل رمز HS نفسه."
            + _MIN_FOUR_SEARCH_ANGLES),
    },
    "consumer_culture": {
        "key": "consumer_culture", "name": "ثقافة المستهلك",
        "mission": "ثقافة الاستهلاك للفئة في السوق المستهدف",
        "allowed_tools": ["web_search", "trends_interest", "trends_context",
                         "lookup_reference", "openalex_search",
                         "eurostat_eu_signals"],
        "instructions": (
            # R3 (ثقافة المستهلك الأعمق): سياق طلب أغنى + زوايا بحث مبنيَنة
            # بلغة السوق — لا رقم اهتمام واحد ولا بحث إنجليزي سطحي.
            "استدعِ trends_context (نداء واحد) لمعرفة ماذا يبحث المستهلك "
            "المحلي فعلاً حول الفئة: الاستعلامات المرتبطة الشائعة والصاعدة، "
            "المواضيع الصاعدة، والتوزيع الإقليمي للاهتمام — اربطها بعادات "
            "الاستهلاك والمناسبات لا تعرضها معزولة. وفي بحث الويب غطِّ "
            "صراحةً بلغة السوق أربع زوايا استهلاكية: (أ) عادات/طقوس "
            "الاستهلاك، (ب) المناسبات والمواسم الشرائية، (ج) ثقافة الطعام/"
            "الاستخدام المحلية، (د) إعلام الجالية/المجتمع المستهدف (منصّات "
            "وقنوات محلية). ما لم يُرصد يُعلَن فجوةً لا انطباعاً. "
            "حلّل: عادات استهلاك الفئة، البُعد الديني (الحلال — استخدم نسبة "
            "المسلمين من lookup_reference جدول demographics)، المواسم "
            "(رمضان/الأعياد)، تفضيلات التغليف واللغة، حساسية بلد المنشأ "
            "(كيف ينظر السوق للمنتجات السعودية). افصل الحقيقة الموثّقة عن "
            "الانطباع صراحةً. "
            "إن كان السوق المستهدف من دول الاتحاد الأوروبي/EFTA، استدعِ "
            "eurostat_eu_signals أيضاً (نداء واحد which='both' يكفي) — "
            "حصة إنفاق الغذاء من مسح ميزانية الأسرة إشارة قوة إنفاق فعلية "
            "على هذه الفئة (لا مجرد الدخل للفرد)، وعدد السكان المولودين "
            "خارج السوق إشارة تكميلية لحجم الجاليات المهاجرة — رقم مطلق "
            "لا نسبة (حساب النسبة يتطلب قسمته على السكان الكلي من بعثة "
            "demographics_economy المعزولة، خارج نطاقك هنا؛ اذكر الرقم "
            "المطلق فقط وصرّح أن حساب النسبة يتطلب دمجه لاحقاً). خارج "
            "أوروبا لا تستدعِ هذه الأداة إطلاقاً — ستعيد امتناعاً معلناً "
            "بلا فائدة."
            + _MIN_FOUR_SEARCH_ANGLES),
    },
    "trade_flow": {
        "key": "trade_flow", "name": "تدفقات التجارة",
        "mission": "حجم استيراد السوق ومساراته لرمز HS هذه المهمة",
        "allowed_tools": ["comtrade_imports"],
        "instructions": (
            # ترقية المرحلة ٢ب: كانت التعليمات تطلب "نمو خلال ٥ سنوات إن
            # توفرت" بلا إلزام الأداة بجلب خمس سنوات فعلياً — الافتراض
            # الصامت ٣ سنوات (comtrade_imports بلا معامل years) فيُعلَن
            # "غير متوفر" لسنوات لم تُطلَب أصلاً لا لأنها غير موجودة
            # فعلياً. و"الموسمية" حُذفت — بيانات كومتريد هنا سنوية
            # إجمالية، لا تكشف موسمية داخل السنة إطلاقاً (تلك مهمة
            # demand_trends حصراً عبر Google Trends).
            "استدعِ comtrade_imports بمعامل years يغطي آخر خمس سنوات كاملة "
            "صراحة (مثال: [آخر سنة-٤، آخر سنة-٣، آخر سنة-٢، آخر سنة-١، "
            "آخر سنة]) — لا تكتفِ بالافتراض (٣ سنوات فقط). احسب من "
            "الناتج: حجم الاستيراد لكل سنة، ونسبة النمو المركّب (CAGR) "
            "عبر السلسلة الخمسية إن توفّرت كاملة. إن أعادت الأداة عدد "
            "سنوات أقل من خمسة (تعذّر جلب/لا سجل)، احسب النمو من السنوات "
            "الفعلية المتوفرة فقط وصرّح صراحة بعدد السنوات الناقصة "
            "وسببها (تعذّر الجلب أم لا سجل) — لا تصف ذلك بأنه 'غير "
            "متوفر' دون تسمية السبب. أعداد فقط مما عاد من الأداة — لا "
            "تقدير لسنة غير مجلوبة، ولا ادّعاء موسمية من هذه الأرقام "
            "السنوية."),
    },
    "demographics_economy": {
        "key": "demographics_economy", "name": "الديموغرافيا والاقتصاد الكلي",
        "mission": "سكان واقتصاد السوق المستهدف وربطهما بحجم الشريحة المستهدَفة",
        "allowed_tools": ["worldbank_indicator", "imf_indicator",
                          "lookup_reference"],
        "instructions": (
            "اجمع: السكان ونموهم، الدخل للفرد وPPP، نسبة الشباب إن أمكن، "
            "ونسبة المسلمين (lookup_reference جدول demographics). اربطها "
            "بحجم الشريحة المستهدَفة للمنتج — احسب لا تُقدّر. "
            # الموجة: دمج مصادر جديدة — IMF WEO للاقتصاد الكلي بجانب البنك الدولي.
            "أضِف صورة الاقتصاد الكلي من imf_indicator: نمو الناتج الحقيقي "
            "(indicator='gdp_growth') والتضخم (indicator='inflation') — كل "
            "قيمة موسومة بمصدرها (IMF WEO) وسنتها؛ الفشل فجوة معلنة لا تقدير."),
    },
    "competitors": {
        "key": "competitors", "name": "تحليل المنافسين",
        "mission": "الدول والشركات المنافسة في السوق المستهدف",
        "allowed_tools": ["comtrade_competitors", "comtrade_imports", "web_search"],
        "instructions": (
            "بلاغ حي (الموجة ١١: تشغيلة إسبانيا أظهرت هذا القسم بتغطية "
            "0.0 رغم توفر بيانات كومتريد الثنائية دوماً): **استدعِ "
            "comtrade_competitors أولاً وقبل أي بحث ويب** — يعيد الدول "
            "المورّدة بالاسم الحقيقي وحصصها ومؤشر تركّز HHI مباشرة من "
            "كومتريد (لا يعتمد على الشبكة العامة، متاح دوماً إن وُجدت "
            "بيانات ثنائية). اكتب الصورة التنافسية على مستوى الدول "
            "(الحصص + HHI) من هذه الأداة **دائماً** — هذا القسم يُمنَع أن "
            "يكون شبه فارغ حتى لو تعذّر رصد أسماء شركات. بعدها ابحث عن "
            "أسماء شركات/علامات فعلية عبر أربعة استعلامات ويب مختلفة "
            "الزاوية على الأقل (اسم دولة مورّدة + السلعة، دليل مستوردين/"
            "موزّعين، معارض تجارية للقطاع، منصات B2B) — كل اسم شركة موسوم "
            "'غير موثَّقة'. لا تكرّر بحث الأسعار — ذاك عمل pricing_scout."
            + _MIN_FOUR_SEARCH_ANGLES),
    },
    "customs_requirements": {
        "key": "customs_requirements", "name": "الاشتراطات الجمركية",
        "mission": "قائمة تحقق دخول السوق ومتطلبات المنشأ السعودي",
        "allowed_tools": ["lookup_reference", "web_search"],
        "instructions": (
            "المرجع الثابت (lookup_reference جدول requirements) هو المصدر "
            "الأساس — اعرضه كما هو أولاً. استخدم بحث الويب فقط للتحقق "
            "المستهدَف من تحديثات حديثة، لا لاكتشاف اشتراطات من الصفر. "
            "اذكر شهادات الحلال/SONCAP/CIQ/SFDA متى انطبقت. إن أعاد "
            "lookup_reference صفراً من الصفوف لهذا السوق/الفئة، لا تكتفِ "
            "بالصمت — أعلن الفجوة صراحة باسم السوق والفئة تحديداً، فجوة "
            "قابلة للسدّ لاحقاً بإضافة صف مرجعي موثّق (لا اختلاقه هنا)."),
    },
    "tariffs_agreements": {
        "key": "tariffs_agreements", "name": "التعريفات الجمركية والاتفاقيات التجارية",
        "mission": "التعريفة الجمركية المطبَّقة وأثر اتفاقيات التجارة",
        "allowed_tools": ["wits_tariff", "lookup_reference"],
        "instructions": (
            "التعريفة المطبَّقة من wits_tariff، وعضوية الاتفاقيات من "
            "lookup_reference جدول agreements (GAFTA/OIC/AfCFTA/GCC/WTO). "
            "إن كانت التعريفة المطبَّقة أدنى من المتوقع MFN، سمِّها "
            "'تفضيل محتمل — تحقق' لا حقيقة مؤكدة."),
    },
    "logistics": {
        "key": "logistics", "name": "اللوجستيات",
        "mission": "جاهزية اللوجستيات وأفضل ميناء ملائم",
        "allowed_tools": ["worldbank_indicator", "lookup_reference", "web_search"],
        "instructions": (
            "مؤشر أداء اللوجستيات (worldbank_indicator indicator="
            "logistics_lpi) وأفضل ميناء ملائم من jeddah/dammam "
            "(lookup_reference جدول ports للسوق المستهدف). خطوط شحن منشورة "
            "إن وُجدت عبر بحث الويب. زمن/تكلفة الشحن غير المرصودين = فجوة "
            "معلنة، لا تقدير."),
    },
    "channels_importers": {
        "key": "channels_importers", "name": "قنوات التوزيع والاستيراد",
        "mission": "أبواب الدخول الفعلية للسوق المستهدف",
        "allowed_tools": ["channels_importers", "web_search"],
        "instructions": (
            "أبواب الدخول: مستورد/موزّع/تجزئة/تجارة إلكترونية/معارض "
            "تجارية. المرشّحون بالاسم من channels_importers يُوسَمون "
            "'غير موثَّقين — التحقق عبر التعميق'." + _MIN_FOUR_SEARCH_ANGLES),
    },
    "demand_trends": {
        "key": "demand_trends", "name": "اتجاهات الطلب والموسمية",
        "mission": "اتجاه الطلب والموسمية للمنتج في السوق المستهدف",
        "allowed_tools": ["trends_interest", "trends_context",
                         "faostat_supply", "openalex_search"],
        "instructions": (
            "لا تكتفِ بنداء trends_interest واحد — نداء واحد لا يكفي "
            "لتحليل حقيقي. نفّذ على الأقل: (١) مصطلح المنتج بـtimeframe="
            "'today 5-y' لاتجاه خمس سنوات، (٢) نفس المصطلح بـtimeframe="
            "'today 12-m' لموسمية العام الأخير، (٣) مصطلح موسمي مرتبط "
            "('رمضان <المنتج>' أو مناسبة السوق المكافئة) بـ'today 12-m'، "
            "(٤) مصطلح علامة/فئة بديل. قارن الأربعة صراحة (هل الاهتمام "
            "الموسمي أعلى من السنوي؟ هل الاتجاه صاعد/هابط عبر ٥ سنوات؟) — "
            "لا تعرض رقماً واحداً معزولاً. "
            # R3: محرّكات الطلب وراء الرقم — استعلامات/مواضيع صاعدة وتوزيع إقليمي.
            "استدعِ trends_context (نداء واحد) للاستعلامات المرتبطة الشائعة "
            "والصاعدة والمواضيع الصاعدة والتوزيع الإقليمي — تكشف **محرّكات** "
            "الطلب لا حجمه فقط (ماذا يبحث المستهلك حول الفئة، وأين يتركّز "
            "الاهتمام داخل السوق). نصيب الفرد من السلعة "
            "(faostat_supply) إن كان المنتج غذائياً. "
            # بلاغ حي إنتاجي (تمور/هولندا): استعلام openalex_search بالاسم
            # العربي للمنتج أو بمزيج ضيّق (منتج+سوق حرفياً) عاد بلا نتائج —
            # فهرس OpenAlex أدبيّ أكاديمي إنجليزي غالباً، فاستعلام عربي أو
            # ضيّق جداً يفشل المطابقة بنيوياً لا لغياب أدبيات فعلاً.
            "openalex_search اختياري لأدبيات استهلاك/سوق ذات صلة إن وُجدت — "
            "استعلِم بمصطلحات إنجليزية عامة للفئة (اسم الفئة الغذائية/"
            "الاستهلاكية بالإنجليزية + 'consumption'/'demand trends'، لا "
            "الاسم العربي ولا مزيجاً ضيّقاً بالسوق المحدد حرفياً)؛ وسّع "
            "المصطلح (فئة أعمّ) إن أعاد الاستعلام الأول صفر نتائج قبل "
            "الاستسلام. نتيجة فارغة حقيقية بعد ذلك تبقى فجوة معلنة، لا "
            "عطلاً تقنياً."),
    },
    "risk_news": {
        "key": "risk_news", "name": "تقييم المخاطر والمستجدات",
        "mission": "الاستقرار السياسي ومخاطر العملة وآخر الأخبار القطاعية",
        "allowed_tools": ["worldbank_indicator", "imf_indicator", "gdelt_news",
                          "web_search", "openalex_search"],
        "instructions": (
            "الاستقرار السياسي وسيادة القانون وجودة التنظيم "
            "(worldbank_indicator political_stability/rule_of_law/"
            "regulatory_quality)، وتقلّب سعر الصرف: استدعِ "
            "worldbank_indicator indicator='exchange_rate' لثلاث سنوات "
            "مختلفة على الأقل (year=آخر سنة، سنة-١، سنة-٢) واحسب نسبة "
            "التغيّر بينها صراحة — لا تخمين ولا 'تقلّب' بلا سلسلة سنوات "
            "فعلية تدعمه. "
            # الموجة: دمج مصادر جديدة — IMF WEO يثري صورة المخاطر الكلية.
            "وأضِف مؤشرات صندوق النقد الدولي عبر imf_indicator: نمو الناتج "
            "الحقيقي (indicator='gdp_growth')، التضخم (indicator='inflation')، "
            "ورصيد الحساب الجاري (indicator='current_account') — إشارات هشاشة/"
            "متانة كلية موسومة بمصدرها (IMF WEO) وسنتها؛ الفشل فجوة معلنة. "
            "وأهم ١٠ عناوين قطاعية من GDELT آخر ١٢ شهراً "
            "(عنوان/تاريخ/رابط) — إن أعاد GDELT فجوة معلنة (فشل متكرر لا "
            "نتائج)، استخدم web_search كبديل موثَّق: نفّذ عدة استعلامات "
            "أخبار بلغة السوق (اسم السوق + المنتج/القطاع + 'أخبار'/"
            "'news'، وأخرى بمرادفات) حتى تُجمِّع **خمسة عناوين مؤرَّخة "
            "برابط على "
            "الأقل** — إن تعذّر بلوغ الخمسة رغم المحاولة، أعلن فجوة تسمّي "
            "المصدر الذي فشل تحديداً (GDELT 429/شبكة، أم لا نتائج ويب "
            "ذات صلة) بدل الاستسلام الصامت أو عرض أقل من خمسة كأنه كافٍ. "
            "openalex_search اختياري لأدبيات أكاديمية/تجارية عن مخاطر "
            "القطاع إن وُجدت." + _MIN_FOUR_SEARCH_ANGLES),
    },
    "opportunity_gaps": {
        "key": "opportunity_gaps", "name": "الفرص الاستراتيجية والفجوات",
        "mission": "تركيب الفرص والفجوات من تقارير الوكلاء ١-١١ (يعمل أخيراً)",
        "allowed_tools": ["openalex_search"],
        "instructions": (
            "مصدرك الأساس نتائج الوكلاء الأحد عشر السابقين (مُرفَقة) — "
            "اقرأها أولاً. استخرج: طلباً غير ملبّى، مورّدين يفقدون حصتهم، "
            "مزايا سعودية (قرب، اتفاقية، حلال)، وفجوات بيانات تستحق "
            "التعميق. openalex_search اختياري فقط لسند أدبي إضافي على فرصة "
            "رصدتها فعلاً من التقارير المرفقة — لا لاكتشاف فرص من الصفر. "
            "كل استنتاج يستشهد بمعرّف نقطة بيانات من التقارير المرفقة أو "
            "نتيجة أداة فعلية — لا استنتاج بلا سند."),
    },
}

# ترتيب التشغيل الثابت — the fixed run order (12 runs last, reads 1-11).
MISSION_ORDER: tuple[str, ...] = (
    "pricing_scout", "consumer_culture", "trade_flow", "demographics_economy",
    "competitors", "customs_requirements", "tariffs_agreements", "logistics",
    "channels_importers", "demand_trends", "risk_news", "opportunity_gaps",
)

# النطاقات المُفضَّلة لكل بعثة (الموجة: دمج مصادر جديدة، Wave 2) — مواقع
# محتوائية بلا API تُدمَج عبر انحياز بحثٍ site: (لا كشط). نتائجها تُرتَّب أولاً
# وتُوسَم دليلاً ثانوياً ◐ برابط. المصدر يبقى «بحث ويب» (Serper) — لا استخراج
# محتوى جُملة (انضباط حقوق النشر). المفتاح = مفتاح البعثة، وكل مفتاح **يجب** أن
# يملك أداة web_search في allowed_tools (وإلا كان الانحياز إعداداً ميتاً — درس
# ٩؛ يُنفَّذ في tests/test_wave_datasources_integration.py). لذا الاقتصاد الكلي
# النقيّ (demographics_economy، أرقام WB/IMF بلا بحث) لا يظهر هنا — انحياز
# TradingEconomics الكلي/الخطري يركب بعثة risk_news التي تحمل web_search.
#   • ثقافة المستهلك     → globalbusinessculture.com (ثقافة أعمال/استهلاك)
#   • الاشتراطات الجمركية → ccacoalition.org (امتثال بيئي/مناخي — ثانوي)
#   • المخاطر            → ccacoalition.org + tradingeconomics.com (استشهاد
#     فقط — TradingEconomics واجهته مدفوعة والكشط مخالف لشروطه، فلا جلب جماعي)
PREFERRED_DOMAINS: dict[str, list[str]] = {
    "consumer_culture": ["globalbusinessculture.com"],
    "customs_requirements": ["ccacoalition.org"],
    "risk_news": ["ccacoalition.org", "tradingeconomics.com"],
}

# صفوف الكتالوج الإضافية — additive AGENT_CATALOG rows (لوحة «إعدادات
# الوكلاء»)، مسجَّلة عند استيراد هذا الملف. مفاتيح مختلفة عن الـ١٤ القائمة
# فلا تصادم؛ paid=False (تستهلك عدّاد SILK_PAID_DAILY_CAP لإضافات الذكاء
# الاصطناعي عبر silk_ai_judge.ai_extras_blocked — نفس بوابة consumer/dynamics
# القائمة، لا بوابة PAID الجديدة).
_CATALOG_ROWS = [
    {"key": m["key"], "name": m["name"], "role": f"{m['mission']} · Claude+أدوات",
     "paid": False}
    for m in MISSIONS.values()
]
silk_agents.register_agents(_CATALOG_ROWS)

# ميزانية وكيل واحد ضمن تشغيلة الاثني عشر — أخفض من ميزانية silk_llm_runtime
# الافتراضية (٨) لأن ١١ وكيلاً يعملون معاً؛ يبقى قابلاً للضبط بيئياً.
_MISSION_BUDGET = {
    "tool_calls": int(os.environ.get("SILK_MISSION_TOOL_CALLS", "5")),
    "max_output_tokens": int(os.environ.get("SILK_MISSION_MAX_TOKENS", "4000")),
}
# ميزانية أعمق للبعثات المُلزَمة بأربعة+ استعلامات مختلفة الزاوية أو
# ٤+ نداءات ترندز (P0-2، الموجة ٩) — ٥ نداءات كانت تكفي بالكاد نداءً
# سطحياً واحداً، لا التعميق المطلوب الآن؛ محدودة لهذه البعثات فقط، لا
# رفع عام يُبطئ التشغيلة الكاملة بلا داعٍ.
_DEEP_RESEARCH_MISSION_BUDGET = {
    "tool_calls": int(os.environ.get("SILK_DEEP_MISSION_TOOL_CALLS", "9")),
    "max_output_tokens": _MISSION_BUDGET["max_output_tokens"],
}
_DEEP_RESEARCH_MISSIONS = frozenset({
    "pricing_scout", "consumer_culture", "channels_importers",
    "competitors", "risk_news", "demand_trends",
    # ترقية المرحلة ٢ب: خمس نداءات comtrade_imports صريحة (سنة واحدة لكل
    # نداء تقريباً) تستهلك تقريباً كامل الميزانية الافتراضية (٥) بلا
    # هامش لإعادة محاولة عند فشل جلب سنة واحدة.
    "trade_flow"})


def _budget_for(key: str) -> dict:
    """ميزانية البعثة — أعمق للستّ المُلزَمة بتعدد الاستعلامات، الافتراضي
    لغيرها (بما فيها opportunity_gaps رغم امتلاكه أداة اختيارية واحدة)."""
    return (_DEEP_RESEARCH_MISSION_BUDGET if key in _DEEP_RESEARCH_MISSIONS
           else _MISSION_BUDGET)


_MISSION_TIMEOUT_S = int(os.environ.get("SILK_MISSION_TIMEOUT_S", "90"))


def _report_is_failed(report: object) -> bool:
    """علم فشل بعثة سواء كانت `AgentReport` حيّاً أو dict مُفكَّكاً — نقطة
    قلق المالك (بلاغ Nadec/اليمن #7): `getattr(dict, "failed", False)` يعيد
    False صامتاً لقاموس فيخفي فشلاً، فيتخطّى الاستئنافُ بعثةً فاشلة. اليوم
    `load_mission_checkpoints` يعيد `AgentReport` (لا dict)، لكن الاعتماد
    على ذلك ضمنيّ هشّ — هذا الحارس يجعل قرار الإعادة صحيحاً مهما كان الشكل."""
    if isinstance(report, dict):
        return bool(report.get("failed"))
    return bool(getattr(report, "failed", False))


def _timed_out_report(key: str) -> AgentReport:
    return AgentReport(
        f"LLMMissionAgent:{key}", [], True,
        f"{key}: تجاوز المهلة الزمنية ({_MISSION_TIMEOUT_S}s) — استثناء "
        "لا يوقف بقية الوكلاء (ThreadPoolExecutor)")


def _product_card_context(product_card: dict | None) -> str:
    """سياق سردي غير قابل للاستشهاد من بطاقة المنتج — بلاغ حي (الموجة ٩):
    البطاقة كانت تُجمَع في الواجهة/النموذج ولا تصل أي بعثة أو المحلل
    إطلاقاً (تحليل الموقع التنافسي غائب تماماً عن /research). لا حساباً
    هنا — عرض حقائق البطاقة فقط؛ الحساب (الهامش عند المضاهاة) يقع عند
    المحلل الشامل الذي يستشهد بمعرّفات نقاط بيانات فعلية."""
    if not product_card:
        return ""
    c = product_card
    parts = [f"تكلفة الوحدة: {c.get('cost_per_unit')} "
            f"{c.get('unit') or 'وحدة'}"]
    if c.get("own_price") is not None:
        parts.append(f"السعر المستهدف: {c['own_price']}")
    if c.get("tier"):
        parts.append(f"الفئة: {c['tier']}")
    if c.get("monthly_capacity") is not None:
        parts.append(f"الطاقة الشهرية: {c['monthly_capacity']}")
    if c.get("shipping_per_unit") is not None:
        parts.append(f"الشحن المُقدَّر للوحدة: {c['shipping_per_unit']}")
    if c.get("certifications"):
        parts.append("الشهادات: " + "، ".join(map(str, c["certifications"])))
    return ("بطاقة منتج المستخدم (سياق فقط — ليست نقطة بيانات مُستشهَداً "
           "بها، استخدمها في التحليل السردي/الحسابي لا كاستشهاد): "
           + "؛ ".join(parts))


def _checkpoint(analysis_id: int | None, key: str, report: AgentReport,
                market_iso3: str | None = None) -> None:
    """خزّن نقطة تفتيش بعثة فور اكتمالها — no-op بلا analysis_id (استدعاء
    مكتبي مباشر خارج /research، أو `persist=False`). فشل التخزين لا يُسقط
    التشغيلة — نفس مبدأ عدّادات silk_context (قناة جانبية لا شرط).

    `market_iso3` (البلاغ الحي — تسرّب اليمن↔الكويت، 2026-07-21): يُختَم
    على الصفّ فترفض `load_mission_checkpoints` لاحقاً أيّ استئنافٍ لسوقٍ آخر
    يستهلك نتيجة هذه البعثة بالخطأ.

    التقدّم الحيّ (تدقيق تجربة المستخدم): نفس لحظة اكتمال كل بعثة أيضاً
    لقطةُ تقدّمٍ («المرحلة: بعثات»، عدّادات llm_calls/tool_calls الحالية) —
    هذا الحلقة (المُنسِّقة في `run_all_missions`، لا خيوط العمل) تُنفَّذ في
    نفس السياق (contextvar) الذي بدأ العدّاد، فتقرأ التراكم الحيّ من خيوط
    البعثات (نفس كائن القاموس المُشترَك عبر `copy_context`، لا نسخة جامدة)."""
    if analysis_id is None:
        return
    try:
        import silk_storage
        silk_storage.save_mission_checkpoint(analysis_id, key, report,
                                             market_iso3=market_iso3)
    except Exception as e:  # noqa: BLE001 — نقطة التفتيش تحسين لا شرط تشغيل
        log.warning("checkpoint write failed for %s/%s: %s", analysis_id, key, e)
    import silk_context
    silk_context.snapshot_research_progress(analysis_id, "missions")


# ── D3 (SPEC-v2): جلب حوكمة حتمي لبعثة risk_news ─────────────────────────
# §9 «تقييم المخاطر» كانت تفتقد أرقام WGI (الاستقرار السياسي/سيادة القانون/
# جودة التنظيم): بعثة risk_news تعتمد كلّياً على أن يستدعي كلود أداة
# worldbank_indicator للمؤشرات الثلاثة — إن أغفل واحداً خرج §9 ناقصاً
# (خطأ ربط/إظهار البعثة، لا خطأ جلب — الجلب مُصلَح ومقفول، تدقيق #1).
# الإصلاح: جلب حتمي للثلاثة يُلحَق بحقائق البعثة بعد تشغيلها — حاضرة دائماً
# حين ينجح الجلب، فجوة معلنة (None، ثقة 0.0) حين يفشل. لا اختلاق. يشمل
# RL.EST (سيادة القانون) الذي يغفله حتى RiskAgent في مسار /analyze.
_WGI_GOVERNANCE = (
    ("PV.EST", "political_stability", "الاستقرار السياسي (WGI)"),
    ("RL.EST", "rule_of_law", "سيادة القانون (WGI)"),
    ("RQ.EST", "regulatory_quality", "جودة التنظيم (WGI)"),
)


def _wgi_governance_datapoints(iso3: str) -> list:
    """المؤشرات الثلاثة كـDataPoints — قيمة من المخزن أولاً ثم World Bank
    الحيّ (source=3 مُسجَّل في silk_data_layer)، أو فجوة معلنة عند الفشل.
    كل ملاحظة موسومة [risk] كي يربطها كاتب §9 ببعثة المخاطر."""
    from silk_data_layer import DataPoint, world_bank
    out: list = []
    for ind, _metric, label in _WGI_GOVERNANCE:
        got = None
        try:
            import silk_store
            got = silk_store.get_indicator(iso3, ind)
        except Exception:  # noqa: BLE001 — المخزن تحسين لا شرط
            got = None
        if got and got.get("value") is not None:
            out.append(DataPoint(
                round(float(got["value"]), 3),
                got.get("source", "World Bank"),
                float(got.get("confidence") or 0.9),
                f"[risk] {label} — {ind} سنة {got.get('year')} (مخزن الحقائق)"))
            continue
        try:
            dp = world_bank(iso3, ind)
        except Exception as e:  # noqa: BLE001 — فشل الجلب = فجوة معلنة لا كسر
            dp = DataPoint(None, "World Bank", 0.0,
                           f"{ind} تعذّر: {type(e).__name__}")
        if dp.value is not None:
            out.append(DataPoint(dp.value, dp.source, dp.confidence,
                                 f"[risk] {label} — {ind} ({dp.note})"))
        else:
            out.append(DataPoint(None, dp.source or "World Bank", 0.0,
                                 f"[risk] {label} غير متاح — {ind} ({dp.note})"))
    return out


def _augment_risk_news_wgi(report: AgentReport, iso3: str) -> AgentReport:
    """ألحِق مؤشرات WGI الثلاثة بحقائق بعثة risk_news إن لم تكن حاضرة أصلاً
    (مطابقة على رمز المؤشر في ملاحظات البنود — لا تكرار ما رصده كلود، ولا
    استبدال بنده) — §9 تحصل على أرقام الحوكمة حتماً لا اعتماداً على نداء
    كلود وحده."""
    if not iso3:
        return report
    findings = getattr(report, "findings", None)
    if findings is None:
        return report
    existing = " ".join(str(getattr(dp, "note", "")) for dp in findings)
    for dp in _wgi_governance_datapoints(iso3):
        code = next((c for c, _m, _l in _WGI_GOVERNANCE if c in str(dp.note)), "")
        if code and code in existing:
            continue  # كلود رصد هذا المؤشر فعلاً — لا تكرار
        findings.append(dp)
    return report


def run_all_missions(market: MarketRef, product: str = "",
                     hs_code: str | None = None,
                     product_card: dict | None = None,
                     analysis_id: int | None = None,
                     resume_reports: dict[str, AgentReport] | None = None,
                     ) -> dict[str, AgentReport]:
    """شغّل البعثات الاثنتي عشرة — missions 1-11 in parallel (ThreadPoolExecutor,
    المستودع متزامن — لا asyncio)، ثم opportunity_gaps (12) قارئاً نتائجها.

    فشل/مهلة وكيل واحد = تقرير فاشل موسوم لا يوقف البقية (نفس مبدأ
    `ResearchManager.distribute`). `product_card`: بطاقة منتج اختيارية
    (الموجة ٩) — تصل كل بعثة كسياق سردي (extra_context)، خصوصاً
    pricing_scout (سلّم الأسعار) وopportunity_gaps. Returns {mission_key:
    AgentReport}.

    نقطة تفتيش/استئناف (P0، حادثة نفاد الاعتمادات): `analysis_id` يفعّل
    تخزين كل بعثة **فور اكتمالها** (`_checkpoint`) لا بعد التشغيلة كاملة —
    عملية تُقتَل منتصف الطريق (عطل/إعادة نشر/تجاوز مهلة البوابة) لا تخسر
    البعثات المكتملة فعلاً. `resume_reports`: نتائج مُحمَّلة مسبقاً من
    استئناف سابق — مفاتيحها تُستثنى من إعادة التشغيل والتخزين تماماً
    (لا نداء كلود جديد لبعثة مكتملة بالفعل).
    """
    reports: dict[str, AgentReport] = dict(resume_reports or {})
    parallel_keys = [k for k in MISSION_ORDER if k != "opportunity_gaps"]
    # بلاغ Nadec/اليمن #7: نقطة تفتيش **فاشلة** (429/مهلة — `failed=True`)
    # ليست إنجازاً — كان `k not in reports` وحده يتخطّاها فيعيد resume صفر
    # نداء وتبقى البعثة فاشلة للأبد. الفاشلة تُعاد؛ الناجحة تبقى بلا إعادة
    # دفع (نقطة التفتيش الجديدة تستبدل القديمة عند الاكتمال — upsert).
    to_run = [k for k in parallel_keys
              if k not in reports or _report_is_failed(reports[k])]
    card_ctx = _product_card_context(product_card)

    def _run_one(key: str) -> AgentReport:
        agent = LLMMissionAgent(MISSIONS[key])
        return agent.run({"market": market, "product": product,
                          "hs_code": hs_code, "budget": _budget_for(key),
                          "instruction": "", "extra_context": card_ctx})

    # نسخ سياق contextvars الحالي قبل التفريع — ThreadPoolExecutor لا يرث
    # contextvars تلقائياً (خلاف asyncio)، فبلا هذا النسخ تفقد الخيوط
    # الموازية بصمت: توجيهات لوحة إعدادات الوكلاء (agent_prefs_context)،
    # حجب إضافات كلود (block_ai_extras)، وعدّاد llm_calls/tool_calls —
    # ثلاثتها contextvars يضبطها استدعاء api.py الخارجي. اكتُشف تجريبياً
    # أثناء بناء هذه الموجة (لا مجرد نظري): بلا `ctx.run` كل خيط يرى القيم
    # الافتراضية بصمت (توجيه مُتجاهَل، حجب غير سارٍ) — فشل صامت خطير.
    # نسخة Context **مستقلة لكل مهمة** — كائن Context واحد لا يقبل `.run()`
    # من أكثر من خيط في آن (RuntimeError: "already entered")؛ copy_context()
    # من الخيط الرئيسي نفسه لكل مهمة تعطي لقطات مستقلة آمنة للتوازي.
    import contextvars
    import time as _time

    if to_run:
        with ThreadPoolExecutor(max_workers=len(to_run)) as pool:
            futures = {pool.submit(contextvars.copy_context().run, _run_one, k): k
                      for k in to_run}
            # تفتيش تدريجي حقيقي (P0): `wait(..., FIRST_COMPLETED)` في حلقة
            # بدل `futures.items()` المتسلسل — الأخير كان يحجب على أول
            # بعثة بترتيب الإرسال حتى مهلتها كاملة قبل حتى النظر لبعثة
            # ثانية أنجزت فعلاً قبلها، فيؤخّر تخزين نقاط تفتيش جاهزة فعلاً
            # (بلاغ حي: عطل بين الثانية ٥ والثانية ٨٧ من نافذة ٩٠ ثانية كان
            # سيخسر بعثة اكتملت في الثانية ٥ لأنها لم تُخزَّن بعد). مهلة
            # واحدة مشتركة للدفعة كاملة (لا مهلة منفصلة لكل بعثة بالتتابع)
            # — البعثات فعلاً متوازية فنافذة الانتظار الكلية تقارب مهلة
            # بعثة واحدة، لا مجموعها.
            deadline = _time.monotonic() + _MISSION_TIMEOUT_S
            pending = set(futures)
            while pending:
                remaining = deadline - _time.monotonic()
                if remaining <= 0:
                    break
                done, pending = cf_wait(pending, timeout=remaining,
                                        return_when=cf_FIRST_COMPLETED)
                for fut in done:
                    key = futures[fut]
                    try:
                        report = fut.result()
                    except Exception as e:  # noqa: BLE001 — عزل الأعطال، لا سقوط جماعي
                        log.warning("mission %s raised: %s", key, e)
                        report = AgentReport(
                            f"LLMMissionAgent:{key}", [], True,
                            f"{key}: خطأ غير متوقع: {type(e).__name__}: {e}")
                    reports[key] = report
                    _checkpoint(analysis_id, key, report,
                               getattr(market, "iso3", None))
            for fut in pending:  # لم تُنجز قبل انتهاء المهلة المشتركة
                key = futures[fut]
                log.warning("mission %s timed out after %ss", key, _MISSION_TIMEOUT_S)
                reports[key] = _timed_out_report(key)
                _checkpoint(analysis_id, key, reports[key],
                           getattr(market, "iso3", None))

    if ("opportunity_gaps" not in reports
            or _report_is_failed(reports["opportunity_gaps"])):
        prior_findings = [dp for k in parallel_keys for dp in reports[k].findings]
        gaps_agent = LLMMissionAgent(MISSIONS["opportunity_gaps"])
        reports["opportunity_gaps"] = gaps_agent.run({
            "market": market, "product": product, "hs_code": hs_code,
            "budget": _MISSION_BUDGET, "extra_findings": prior_findings})
        _checkpoint(analysis_id, "opportunity_gaps", reports["opportunity_gaps"],
                   getattr(market, "iso3", None))

    # D3 (SPEC-v2): أرقام WGI للحوكمة تُلحَق حتماً ببعثة المخاطر — §9 لا
    # تعتمد على نداء كلود وحده (تشغيلات كانت تخرج §9 بلا أرقام استقرار/
    # سيادة قانون/تنظيم). يُعاد الحساب كل تشغيلة (مطابقة الرمز تمنع التكرار)،
    # فمسار الاستئناف من نقطة تفتيش بلا WGI يحصل عليها أيضاً.
    if "risk_news" in reports:
        _augment_risk_news_wgi(reports["risk_news"], getattr(market, "iso3", ""))
        # الموجة: دمج مصادر جديدة — IMF WEO (نمو/تضخم/حساب جارٍ) يصل بعثة المخاطر
        # عبر أداة imf_indicator + تعليمات البعثة (لا إلحاق حتمي غير مشروط).
        # قرار مقصود (منسجم مع حساسية التكلفة/السرعة، D-06): لا نضيف ٣ نداءات
        # شبكة متزامنة غير مشروطة لكل تشغيلة كما يفعل D3 للحوكمة — WGI مخزَّن
        # أولاً (رخيص) بينما IMF لا مخزن له، فالإلحاق الحتمي كان يضيف زمناً
        # للمسار الحار في كل تشغيلة. الأداة مُحاكاة في الاختبارات (صفر زمن)
        # وحيّة في الإنتاج (حين يستدعيها كلود، وهو مُوجَّه لذلك في تعليمات
        # البعثة). demographics_economy (الكلي) يحصل على IMF بنفس النمط.
    return reports


def deep_research(market: MarketRef, product: str = "",
                  hs_code: str | None = None, dry_run: bool = False,
                  only_agent: str | None = None,
                  trace_id: str | None = None,
                  trace_dir: str | None = None,
                  product_card: dict | None = None,
                  analysis_id: int | None = None,
                  resume_reports: dict[str, AgentReport] | None = None,
                  ) -> dict:
    """نقطة دخول التنقيح والتشغيل الموحّدة — أداة التنقيح الأساسية (الموجة ٦،
    §docs/TUNING.md): `dry_run=True, only_agent="pricing_scout"` يشغّل
    بعثة **واحدة** فقط ضد سوق حقيقية ويطبع أثرها الكامل (البرومبت، كل
    نداء أداة، البنود المُسقَطة) للطرفية — بلا حرق تشغيلة الاثنتي عشرة
    كاملة. `dry_run=False` (الافتراضي) يشغّل `run_all_missions` كالمعتاد،
    بتتبّع مفعَّل دوماً (data/traces/{trace_id}.jsonl) كي يبقى كل تشغيل
    إنتاجي قابلاً للتدقيق.

    يعيد {"mode": "dry_run"|"full", "trace_id":..., "trace_path":...,
    "reports": {...} أو {"mission": key, "report": AgentReport} للتنقيح}.
    """
    import silk_trace

    tid = trace_id or (
        f"dryrun-{only_agent}-{market.iso3}" if dry_run and only_agent
        else f"run-{market.iso3}-{int(time.time())}")
    trace_kwargs = {"dir_path": trace_dir} if trace_dir else {}

    if dry_run and only_agent:
        if only_agent not in MISSIONS:
            raise ValueError(f"unknown mission {only_agent!r} — "
                             f"available: {sorted(MISSIONS)}")
        with silk_trace.trace_context(tid, **trace_kwargs) as path:
            agent = LLMMissionAgent(MISSIONS[only_agent])
            report = agent.run({"market": market, "product": product,
                               "hs_code": hs_code,
                               "budget": _budget_for(only_agent),
                               "extra_context": _product_card_context(
                                   product_card)})
        events = silk_trace.read_trace(tid, **trace_kwargs)
        log.info("dry-run %s -> %s (%d trace event(s), %s)",
                only_agent, "FAILED" if report.failed else "ok",
                len(events), path)
        for ev in events:
            print(json.dumps(ev, ensure_ascii=False, indent=2))
        return {"mode": "dry_run", "mission": only_agent, "report": report,
               "trace_id": tid, "trace_path": path, "events": events}

    with silk_trace.trace_context(tid, **trace_kwargs) as path:
        reports = run_all_missions(market, product=product, hs_code=hs_code,
                                   product_card=product_card,
                                   analysis_id=analysis_id,
                                   resume_reports=resume_reports)
    return {"mode": "full", "reports": reports, "trace_id": tid,
           "trace_path": path}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from silk_market_resolver import resolve_market
    ref, _ = resolve_market("Nigeria")
    all_reports = run_all_missions(ref, product="تمور", hs_code="080410")
    for key, report in all_reports.items():
        flag = "FAILED" if report.failed else "ok"
        print(f"  [{flag}] {key}: {report.summary}")
