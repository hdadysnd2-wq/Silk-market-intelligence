"""طبقة البيانات الأساسية لسِلك — Silk core data layer.

Real public data only (UN Comtrade preview, World Bank). Never fabricates:
on any failure returns a provenance-tagged None / [] and logs a warning.
"""
from __future__ import annotations

import datetime
import functools
import logging
import os
import re
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)


def _load_dotenv(path: str = ".env") -> None:
    """حمّل مفاتيح من .env إن وُجد — minimal stdlib .env loader (no dependency).

    Fills only env vars that are not already set. Missing file is fine (offline).
    """
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"\''))
    except FileNotFoundError:
        pass


_load_dotenv()

# نقاط النهاية — base URLs of the real sources. Comtrade has TWO surfaces:
#   • preview (/public/v1/preview/...) — بلا مفتاح، محدود الصفوف ومخنوق الطلبات.
#   • data    (/data/v1/get/...)       — مع مفتاح، البيانات الكاملة و~500 طلب/يوم.
# نختار الإنتاج تلقائيًّا متى توفّر المفتاح؛ كلاهما بنفس المسار C/A/HS والمعاملات.
ENDPOINTS = {
    "comtrade": "https://comtradeapi.un.org/public/v1/preview/C/A/HS",
    "comtrade_data": "https://comtradeapi.un.org/data/v1/get/C/A/HS",
    "world_bank": "https://api.worldbank.org/v2",
}

_TIMEOUT = 30


def _timeout_for(host: str) -> float:
    """مهلة الاتصال لكل مصدر (WS4) — World Bank أبطأ عادةً من كومتريد فيأخذ
    نافذةً أوسع؛ الكل قابلٌ للضبط بيئياً بلا كسر توافق (الافتراض = القديم ٣٠).

    Per-source connect/read timeout: the World Bank API is routinely slower
    than Comtrade, so it gets a longer default. All env-tunable; defaults keep
    the historical 30s for anything unrecognized so no caller regresses."""
    if "api.worldbank.org" in host:
        return float(os.environ.get("SILK_WB_TIMEOUT_S", "45"))
    if "comtradeapi.un.org" in host:
        return float(os.environ.get("SILK_COMTRADE_TIMEOUT_S", "30"))
    return float(os.environ.get("SILK_HTTP_TIMEOUT_S", str(_TIMEOUT)))


# مفتاح Comtrade الاختياري — optional free key; switches to the full /data/v1/get
# endpoint and raises the cap to ~500 requests/day. Set COMTRADE_API_KEY in env/.env.
COMTRADE_KEY = os.environ.get("COMTRADE_API_KEY", "").strip()


def _comtrade_url() -> str:
    """اختر سطح كومتريد — full data endpoint when a key is set, else preview."""
    return ENDPOINTS["comtrade_data"] if COMTRADE_KEY else ENDPOINTS["comtrade"]


# جلسة مشتركة بـkeep-alive (P2) — one pooled Session so the ~150 fan-out calls
# per analysis reuse TCP/TLS connections instead of a fresh handshake each.
# stdlib requests فقط (لا httpx، لا تبعية جديدة). الاختبارات الهيرمتية تقطع
# requests.sessions.Session.request فتقطع هذه الجلسة و requests.get معاً (كلاهما
# يمرّ عبر Session.request)، فيبقى قطع الشبكة شاملاً.
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=16, pool_maxsize=16, max_retries=0))


# 1b: مباعدة النداءات لكل مضيف (تحليل واحد يطلق ~150 نداء دفعة واحدة فيضرب
# حدود المعدل) + إعادة محاولة بتراجع أسّي على 429/5xx مع احترام Retry-After.
# الإعادة على أكواد HTTP فقط (الردّ الحقيقي الموثَّق) — أخطاء الاتصال تمرّ
# كما كانت (الاختبارات الهيرمتية تقطع الشبكة فلا حلقات نوم فيها).
import threading as _threading
import time as _time

_host_lock = _threading.Lock()
_last_hit: dict[str, float] = {}
_RETRYABLE = (429, 500, 502, 503, 504)


def _min_gap_ms(host: str) -> float:
    """نافذة التباعد الدنيا بين نداءات نفس المضيف — بلاغ حي (تشغيلة تمور/
    هولندا الثالثة، 429 متكرر من كومتريد): البعثات المتوازية الاثنتا عشرة
    تتشارك نفس المضيف، والنافذة العامة (250مث) أسرع من حد كومتريد الفعلي
    (~نداء/ثانية على سطح المعاينة). كومتريد يحصل على نافذة أوسع خاصة به
    (SILK_COMTRADE_MIN_GAP_MS، افتراضي 1100مث)؛ بقية المضيفين على النافذة
    العامة (SILK_HTTP_MIN_GAP_MS، افتراضي 250مث)."""
    if "comtradeapi.un.org" in host:
        return float(os.environ.get("SILK_COMTRADE_MIN_GAP_MS", "1100"))
    return float(os.environ.get("SILK_HTTP_MIN_GAP_MS", "250"))


def _throttle(host: str) -> None:
    gap = _min_gap_ms(host) / 1000.0
    if gap <= 0:
        return
    with _host_lock:
        wait = _last_hit.get(host, 0.0) + gap - _time.monotonic()
        _last_hit[host] = max(_time.monotonic(), _last_hit.get(host, 0.0) + gap)
    if wait > 0:
        _time.sleep(min(wait, 5.0))


def _backoff_delay(attempt: int, retry_after: str = "") -> float:
    """مهلة إعادة المحاولة — تراجُع أسّي مع تشويش عشوائي (jitter)، بلاغ حي
    (429 متكرر): مهلة حتمية بلا تشويش تجعل النداءات المتوازية الفاشلة معاً
    تعيد المحاولة معاً فتضرب حد المعدل معاً مجدداً. Retry-After من الخادم
    يُحترم كأساس ويُضاف فوقه تشويش صغير يفكّ التزامن."""
    import random
    ra = str(retry_after or "").strip()
    if ra.replace(".", "", 1).isdigit():
        return min(float(ra) + random.uniform(0.0, 0.5), 30.0)
    base = 1.0 * (2 ** attempt)
    return min(base + random.uniform(0.0, base), 30.0)


def _http_get(url: str, params: dict | None = None,
              headers: dict | None = None):
    """جلب مرن عبر الجلسة المجمّعة — throttled GET with 429/5xx backoff (1b).

    `headers` (اختياري): ترويسات لمصادر تمرّر مفتاحاً في ترويسة لا في الاستعلام
    (فلا يظهر السرّ في الـURL)؛ None = سلوك قديم بلا ترويسات إضافية."""
    host = url.split("/")[2] if "://" in url else url
    timeout = _timeout_for(host)
    # WS4: قاطع الدائرة لكل مضيف — إن كان مفتوحاً (N فشلٍ 429/5xx متتالٍ حديثاً)
    # نفشل بسرعة بمحاولةٍ واحدة (بلا حلقة إعادةٍ ونومٍ يعلّق ~١٥٠ نداء fan-out)؛
    # لا يلفّق استجابةً — الطبقة الأعلى تُعلن الفجوة كالمعتاد. نجاحُ المحاولة
    # الاستكشافية يغلق الدائرة (record_success).
    import silk_circuit
    breaker = silk_circuit.http_breaker
    retries = 0 if breaker.is_open(host) else int(
        os.environ.get("SILK_HTTP_RETRIES", "3"))
    resp = None
    for attempt in range(retries + 1):
        _throttle(host)
        # headers شرطيّ: بلا ترويسات يبقى النداء مطابقاً للتوقيع القديم (لا
        # يكسر محاكاة `_session.get` القائمة) — الترويسات مسار WTO الجديد وحده.
        resp = (_session.get(url, params=params, headers=headers,
                             timeout=timeout) if headers is not None
                else _session.get(url, params=params, timeout=timeout))
        if resp.status_code not in _RETRYABLE:
            breaker.record_success(host)
            return resp
        if attempt >= retries:
            # استُنفدت المحاولات على حالةٍ قابلةٍ للإعادة — سجّل فشلاً في القاطع
            # وحدثاً بنيوياً في التتبّع (لا `ops_errors` — قرار عدم إغراق
            # عالي التردّد، docs/EXTERNAL_SERVICES_FAILURE_AUDIT.md).
            breaker.record_failure(host)
            _record_fetch_failure_event(host, url, status=resp.status_code,
                                        attempt=attempt + 1)
            return resp
        delay = _backoff_delay(attempt, resp.headers.get("Retry-After") or "")
        log.warning("HTTP %s from %s — retry %d/%d in %.1fs",
                    resp.status_code, host, attempt + 1, retries, delay)
        _time.sleep(delay)
    return resp


def _record_fetch_failure_event(host: str, endpoint: str, *, status=None,
                                error_repr: str = "", attempt: int = 0) -> None:
    """حدث فشلٍ بنيويّ للجلب (WS4) — {source_id, endpoint, status, error_repr,
    attempt} في التتبّع لكل تشغيلة (no-op صامت خارج سياق تتبّع، تكلفة صفر).

    يستعمل `repr(exc)` لا `exc.args` العارية — فلا سلسلةُ خطأٍ فارغةُ العناصر
    («(), , ()») تظهر أبداً (WS4، نظافة الأخطاء). لا يرفع أبداً."""
    try:
        import silk_trace
        silk_trace.record_event(
            event="source_fetch_failed", source_id=host, endpoint=endpoint,
            status=status, error_repr=error_repr, attempt=int(attempt))
    except Exception:  # noqa: BLE001 — تشخيص لا شرط تنفيذ
        pass


@dataclass
class DataPoint:
    """نقطة بيانات موثّقة — a value plus its provenance."""

    value: object            # actual value, or None when unavailable
    source: str              # e.g. "UN Comtrade", "World Bank"
    confidence: float        # 0.0–1.0
    note: str = ""           # units / year / caveat / failure reason
    retrieved_at: str = ""   # ISO date string
    # 1b (بلاغ مالك: تقرير سنغافورة فارغ بسبب 429 عُرض «لا بيانات»):
    # تمييز بنيوي — "fetch_failed" (تعذّر الجلب: حد معدل/شبكة، أعد المحاولة)
    # مقابل "no_record" (ردّ ناجح بلا سجل فعلاً). "" = غير محدد (سلوك قديم).
    status: str = ""
    # سنة البيانات البنيوية (قرار المالك — «حلِّل المصدر لا النثر»، الدرس ٣٣):
    # فِنتيج الحقيقة كحقلٍ صريح لا كوسمٍ نصّيّ داخل note. الجامعون يضبطونه
    # (البنك الدولي/كومتريد/المنافسون)، و`silk_staleness.fact_year` يقرؤه أولاً
    # فيُقرَّر التقادُم دون تحليل نثرٍ ودون تسريب «year=» لأيّ سطح.
    data_year: "int | None" = None
    # HF1 (بلاغ إسناد مركّب — تقرير قطر ٢٠٢٦-٠٧-٢٣): حين تُسنِد النقطةَ عدّةُ
    # مصادر، تُحفَظ قائمةُ المعرّفات **الذرّية** هنا صراحةً — لا تُدمَج في حقل
    # `source` (الذي يبقى ذرّياً: المصدر الأساسيّ وحده). المستهلِكون (المراجع/
    # المنهجية) يسطّحون هذه القائمة فيُسنِد كلُّ مصدرٍ لرابطه الصحيح، بلا معرّفٍ
    # مركّبٍ يُخطئ الإسناد أو يُكرِّر المصدر. `()` = مصدرٌ واحد (السلوك القديم).
    source_ids: tuple = ()


# السجلّ العمومي لروابط المصادر (§6، أمر العمل الرئيس) — كل مصدرٍ عموميّ مسمّى
# ورابطه الرسمي حيث يتحقّق المدقّق من **مجموعة البيانات** (لا رابط استعلامٍ
# محدّد — لا نختلق رابطًا دقيقًا لا نملكه). المفاتيح بالحروف الصغيرة على الاسم
# القاعدي (قبل أيّ لاحقة عربية بين قوسين). أضِف مصدرًا هنا فقط برابطه الرسمي
# الحقيقيّ — كمرجعٍ ثابتٍ يُعامَل بحذر (كـ data/requirements_l1.csv).
SOURCE_PUBLIC_URL = {
    "un comtrade": "https://comtradeplus.un.org/",
    "comtrade": "https://comtradeplus.un.org/",
    "world bank": "https://data.worldbank.org/",
    "google trends": "https://trends.google.com/trends/",
    "openalex": "https://openalex.org/",
    "faostat": "https://www.fao.org/faostat/en/",
    "eurostat": "https://ec.europa.eu/eurostat/",
    "gdelt": "https://www.gdeltproject.org/",
    # WS8: التِير الأوسط في سلسلة الأخبار (مجاني بلا مفتاح) — مصدرٌ عموميٌّ
    # مسمّى، فيحلّ رابطه الرسمي في قسم المراجع لا كنائبٍ عام (WS9).
    "google news": "https://news.google.com/",
    "wits": "https://wits.worldbank.org/",
    # الموجة: دمج مصادر جديدة — مصدران جديدان بواجهة رسمية (لا كشط).
    "imf weo": "https://www.imf.org/external/datamapper/",
    "wto ttd": "https://ttd.wto.org/",
    # HF1 (سلامة المراجع WS9): أماناتُ الاتفاقيات التجارية مصادرُ مسمّاةٌ ذاتُ
    # روابطَ رسمية (منقولةٌ حرفياً من `data/agreements_l1.csv`، لا اختلاق) —
    # كانت تظهر داخل معرّفٍ مركّبٍ («GCC secretariat، GAFTA secretariat») بلا
    # مدخلٍ ذرّيٍّ مستقلّ، فيُخطئ إسنادُها (تُوجَّه بيانات GAFTA لرابط GCC).
    # المفاتيح الذرّية أدناه تحلّ كلٌّ لرابطه الصحيح.
    "gcc secretariat": "https://gcc-sg.org",
    "gafta secretariat": "https://www.lasportal.org",
    "oic secretariat": "https://www.oic-oci.org",
    "afcfta secretariat": "https://au-afcftahub.au.int",
    "wto secretariat": "https://www.wto.org/",
}


# HF1: الفواصلُ التي كانت تُنتِج معرّفاً **مركّباً** في حقل `source` الذرّيّ.
# `silk_llm_runtime` كان يدمج مصادرَ عدّةٍ بـ«، »؛ والفاصلة المنقوطة «؛»
# فاصلُ ملاحظاتٍ مشابه. **لا** نُدرِج «/» ولا «,» الإنجليزية: أسماءُ مصادرَ
# مفردةٌ مشروعةٌ تحملها («WITS/WTO Tariff»، «BRC/IFS») فليست دمجاً.
_SOURCE_ID_JOIN_RE = re.compile(r"\s*[،؛]\s*")


def is_atomic_source_id(source_id: object) -> bool:
    """هل المعرّف ذرّيّ (مصدرٌ واحد)؟ يرفض أيّ فاصلَ دمجٍ («، » أو «؛») — الفخّ
    الذي أنتج «IMF WEO، World Bank» كمعرّفٍ واحدٍ فأخطأ الإسناد وكرّر المصدر."""
    return not _SOURCE_ID_JOIN_RE.search(str(source_id or ""))


def atomic_source_ids(source: object, source_ids: object = None) -> list:
    """قائمةُ المعرّفات **الذرّية** لبندٍ — يُفضَّل `source_ids` الصريحةُ (HF1)؛
    وإلا يُقسَّم `source` القديم على فاصل الدمج (توافقيةٌ خلفيةٌ لبياناتٍ سابقة).
    فريدةٌ (بلا حساسيةِ حالة) محفوظةُ الترتيب، بلا فراغات؛ وأيُّ معرّفٍ مركّبٍ
    تسرّب — ولو في القائمة الصريحة — يُفكَّك دفاعياً فلا يبقى دمجٌ أبداً."""
    raw: list = []
    if source_ids:
        for s in source_ids:
            t = str(s or "").strip()
            if t:
                raw.append(t)
    if not raw:
        s = str(source or "").strip()
        if s:
            raw = [s]
    flat: list = []
    for x in raw:
        parts = [p.strip() for p in _SOURCE_ID_JOIN_RE.split(x) if p.strip()]
        flat.extend(parts or ([x] if x.strip() else []))
    seen: set = set()
    uniq: list = []
    for x in flat:
        k = x.casefold()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq

# البوّابة العربية للبنك الدولي (الموجة: دمج مصادر جديدة، Wave 3) — **ليست
# مصدر بيانات جديداً**: هي واجهة عربية لنفس قاعدة البنك الدولي المدمَجة أصلاً
# عبر api.worldbank.org. تُستعمل حصراً في سجلّ أدلة **العميل** لسهولة قراءة
# المالك/العميل العربي — رابط تحقّق بلغته لا مصدر رقمٍ ثانٍ (القرار موثَّق في
# docs/DECISIONS.md). المسار الافتراضي/التشغيلي يبقى على data.worldbank.org.
WORLD_BANK_AR_PORTAL = "https://data.albankaldawli.org/"


def public_source_url(source_label: object, arabic: bool = False) -> str:
    """رابطٌ عموميٌّ رسميٌّ للمصدر المسمّى، أو «» إن لم يكن مصدرًا عموميًّا معروفًا.

    لا اختلاق: مصدرٌ مدفوع/بحثٌ/مجهول => «» (المتصل يعرض «—»). يُطابَق الاسمُ
    القاعديُّ (قبل أوّل قوس، بلا لواحق عربية) تطابقًا تامًّا ثمّ ببادئة — فـ
    «UN Comtrade (مخزن الحقائق)» و«World Bank (لقطة مضمّنة)» يُصيبان السجلّ.

    `arabic=True` (Wave 3): استشهادات البنك الدولي في تقرير **العميل** تُوجَّه
    للبوّابة العربية `data.albankaldawli.org` (نفس القاعدة، واجهة عربية أسهل
    قراءةً) — لا يغيّر أي مصدر آخر ولا المسار الافتراضي/التشغيلي."""
    base = str(source_label or "").split("(")[0].strip().lower()
    if not base:
        return ""
    # تطابق تامّ حصرًا: «World Bank» وحده (لواحق الأقواس مُنزَعة أصلًا فـ
    # «World Bank (لقطة)» => «world bank»). **لا** يشمل «World Bank WITS» —
    # WITS أداة تعريفة مستقلّة بوّابتها wits.worldbank.org، لا تُحوَّل للبوّابة
    # العربية (التي لا تعرض جداول WITS بنفس المسار).
    if arabic and base == "world bank":
        return WORLD_BANK_AR_PORTAL
    if base in SOURCE_PUBLIC_URL:
        return SOURCE_PUBLIC_URL[base]
    for key, url in SOURCE_PUBLIC_URL.items():
        if base.startswith(key):
            return url
    return ""


def _today() -> str:
    """تاريخ اليوم — today's ISO date."""
    return datetime.date.today().isoformat()


def _cached_get(url: str, params: dict, ttl_seconds: int = 86400) -> object:
    """جلب مع تخزين مؤقت اختياري — try the on-disk cache, else None (caller falls back).

    Transparent: returns parsed JSON when cache/fetch succeeds, None otherwise so
    the caller keeps its existing direct-GET + graceful-failure behavior. Never
    raises; offline this just returns None (same end result as before).
    """
    try:
        from silk_cache import cached_get
        return cached_get(url, params, ttl_seconds=ttl_seconds,
                          fetcher=_http_get)  # pooled fetch (P2)
    except Exception as e:  # noqa: BLE001 — cache is best-effort, never break the layer
        log.warning("cache layer unavailable (%s); using direct fetch", e)
        return None


# M49 numeric (str) -> ISO3, for World Bank lookups of trade partners.
M49_TO_ISO3 = {
    "682": "SAU", "784": "ARE", "634": "QAT", "414": "KWT", "512": "OMN",
    "048": "BHR", "400": "JOR", "422": "LBN", "818": "EGY", "504": "MAR",
    "788": "TUN", "012": "DZA", "434": "LBY", "729": "SDN", "887": "YEM",
    "368": "IRQ", "364": "IRN", "792": "TUR", "586": "PAK", "356": "IND",
    "050": "BGD", "144": "LKA", "360": "IDN", "458": "MYS", "702": "SGP",
    "764": "THA", "704": "VNM", "608": "PHL", "156": "CHN", "392": "JPN",
    "410": "KOR", "344": "HKG", "158": "TWN", "036": "AUS", "554": "NZL",
    "840": "USA", "124": "CAN", "484": "MEX", "076": "BRA", "032": "ARG",
    "152": "CHL", "170": "COL", "604": "PER", "826": "GBR", "276": "DEU",
    "250": "FRA", "380": "ITA", "724": "ESP", "528": "NLD", "056": "BEL",
    "756": "CHE", "040": "AUT", "752": "SWE", "578": "NOR", "208": "DNK",
    "246": "FIN", "616": "POL", "203": "CZE", "620": "PRT", "300": "GRC",
    "372": "IRL", "643": "RUS", "804": "UKR", "710": "ZAF", "566": "NGA",
    "404": "KEN", "231": "ETH", "288": "GHA", "834": "TZA", "800": "UGA",
}
ISO3_TO_M49 = {v: k for k, v in M49_TO_ISO3.items()}

# الشركاء الخاصون/التجميعيون في كومتريد — Comtrade special/aggregate partner
# codes, ليسوا دولاً ISO فلا يظهرون في countries.csv. قائمة محافظة مقصودة —
# فقط الرموز عالية الثقة من مرجع Comtrade العام والمستقر (partnerAreas)؛
# التوسّع لاحقاً عبر تشغيلة متصلة بالشبكة إن ظهرت رموز إضافية متكررة في
# التتبّع الحي (بلاغ حي، الموجة ١٠: تشغيلة إسبانيا أظهرت رموز شركاء خامة في
# جدول المنافسين — "899" لم يكن مصنَّفاً هناك حتى الآن).
_COMTRADE_SPECIAL_PARTNERS = {
    "0": "World",
    "899": "Areas, nes",
}


def _normalize_m49(code: object) -> str:
    """طبّع رمز M49 لثلاث خانات — "4" و"004" و"0004" جميعها نفس الرمز؛ "0"
    (العالم) يبقى كما هو لا يُبطَّن. غير الرقمي يُعاد كما ورد (لا تحويل)."""
    s = str(code).strip()
    if not s.isdigit():
        return s
    return "0" if int(s) == 0 else s.zfill(3)


@functools.lru_cache(maxsize=1)
def _country_m49_index() -> dict:
    """فهرس M49 → صف دولة من countries.csv (٢٥٠ دولة حقيقية، مlédoze/countries)
    — المصدر الأساس لأسماء شركاء كومتريد (الموجة ١٠، بلاغ حي: قائمة ٧٠ دولة
    مضمَّنة سابقاً كانت تعيد رموزاً خامة لأي دولة خارجها، ٍمثل معظم أسواق
    إفريقيا/أمريكا اللاتينية/آسيا الوسطى)."""
    try:
        from silk_market_resolver import _DEFAULT_PATH, _load
        return {_normalize_m49(r["m49"]): r for r in _load(_DEFAULT_PATH)
                if (r.get("m49") or "").strip()}
    except Exception as e:  # noqa: BLE001 — فهرس تحسيني، فشل = رجوع للخاصين فقط
        log.warning("country m49 index unavailable: %s", e)
        return {}


def partner_name(code: object) -> str:
    """اسم الشريك — countries.csv (٢٥٠ دولة حقيقية) أولاً، ثم رموز كومتريد
    الخاصة/التجميعية، وإلا تسمية معلنة "منطقة غير مصنّفة" بدل رقم خام (بوابة
    الجودة، الموجة ١٠: لا رقم خام حيث يُتوقَّع اسم دولة/شريك)."""
    raw = str(code)
    norm = _normalize_m49(raw)
    row = _country_m49_index().get(norm)
    if row:
        return row.get("name_en") or row.get("name_ar") or raw
    if norm in _COMTRADE_SPECIAL_PARTNERS:
        return _COMTRADE_SPECIAL_PARTNERS[norm]
    if raw.isdigit():
        return f"Unclassified area (Comtrade code {raw})"
    return raw


def primary_value(rec: dict) -> float | None:
    """القيمة الرقمية لسجل كومتريد — the record's numeric 'primaryValue', or None.

    سجل ناقص/مشوّه بلا قيمة رقمية حقيقية **ليس صفراً** — عدّه صفراً اختلاقُ
    رقم (المبدأ التأسيسي)؛ يعيد None ليُسقِطه المستهلك ويعلن الفجوة بدل جمعه.
    A partial/malformed record must never masquerade as a genuine 0: callers
    drop None and declare the gap instead of summing a fabricated zero.
    """
    v = rec.get("primaryValue")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def primary_qty(rec: dict) -> float | None:
    """الوزن الصافي بالكيلوغرام لسجل كومتريد — the record's numeric
    'netWgt' (kg), or None.

    ترقية المرحلة ٢ب: كومتريد يعيد هذا الحقل مع كل سجل تجارة فعلي ولم يكن
    يُستخرَج إطلاقاً — سطر سعر استيراد مرجعي (القيمة/الوزن) كان بالإمكان
    حسابه من مصدر مُستَجلَب أصلاً بلا نداء إضافي. نفس منطق primary_value:
    سجل بلا وزن صافٍ رقمي حقيقي (أو وزن صفري/سالب) **ليس صفراً** — يعيد
    None ليُسقِطه المستهلك بدل قسمة على صفر مختلَق."""
    v = rec.get("netWgt")
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def comtrade_trade(
    hs_code: str,
    reporter_m49: object,
    year: int,
    flow: str = "M",
    partner: object = 0,
) -> list[dict]:
    """تجارة كومتريد — Comtrade preview records, partner codes mapped to names.

    Returns a list of record dicts (each with a 'partnerName' added and
    '_provenance' tag). 1b: fetch failure (429/شبكة) returns **None** —
    ردّ ناجح بلا سجلات يعيد [] — فيميّز المستهلك «تعذّر الجلب» من
    «لا سجل فعلاً» بدل عرض كليهما «لا بيانات».
    """
    # HF4.4 (بلاغ قطر — فجوةُ الوزن): معاينةُ كومتريد تُرجِع **مجموعةَ الأعمدة
    # الكاملة** بما فيها `netWgt` (الوزن الصافي بالكجم) لكلِّ سجل يودعه المُبلِّغ
    # — لا تدعم الواجهةُ انتقاءَ أعمدةٍ ولا نُسقِط الوزن، فهو يصل تلقائياً
    # (`primary_qty` يقرؤه). لذا غيابُ الوزن في الردّ يعني أنّ المُبلِّغ لا يودعه
    # لهذا البند/السنة — لا أنّنا لم نطلبه (المستهلك يُصرِّح الفجوةَ بهذه الدقّة).
    params = {
        "period": str(year),
        "cmdCode": str(hs_code),
        "flowCode": flow,
    }
    # نفس عرف الحذف للطرفين: None/"all" => حذف المعامل فيعيد كومتريد كل
    # الدول (الواجهة ترفض قيمة all الصريحة). reporter=all يخدم «أكبر
    # المستوردين عالمياً» (8c) — صف لكل دولة مبلّغة مع partner=0 (العالم).
    if reporter_m49 not in (None, "all", "ALL"):
        params["reporterCode"] = str(reporter_m49)
    # partner=0 => World total (one row). "all"/None => OMIT partnerCode so Comtrade
    # returns every partner (the API rejects partnerCode=all as invalid).
    if partner not in (None, "all", "ALL"):
        params["partnerCode"] = str(partner)
    if COMTRADE_KEY:
        params["subscription-key"] = COMTRADE_KEY  # full /data endpoint + higher cap
    url = _comtrade_url()
    # سياسة TTL لكل مصدر (M2): سنة تجارية مقفلة تتغيّر نادراً — 30 يوماً؛
    # السنة الجارية 24 ساعة. Per-source TTL: closed years 30d, current 24h.
    ttl = 30 * 86400 if int(year) < datetime.date.today().year else 86400
    try:
        payload = _cached_get(url, params, ttl_seconds=ttl)
        if payload is None:  # cache miss + fetch failed -> same graceful [] as before
            r = _http_get(url, params)
            r.raise_for_status()
            payload = r.json()
        data = payload.get("data") or []
    except Exception as e:  # noqa: BLE001 — never raise to caller
        log.warning("Comtrade fetch failed (%s, reporter=%s, %s): %s",
                    hs_code, reporter_m49, year, e)
        return None  # 1b: تعذّر الجلب ≠ لا سجل — المستهلك يميّز
    prov = {"source": "UN Comtrade", "confidence": 0.9, "retrieved_at": _today()}
    for rec in data:
        rec["partnerName"] = partner_name(rec.get("partnerCode"))
        rec["_provenance"] = prov
    return data


def comtrade_trade_mirror_total(hs_code: str, market_m49: object, year: int,
                                flow: str = "M") -> float | None:
    """إجمالي تقدير مرآة لسوق لا يُبلِغ كومتريد عن نفسه — mirror fallback
    (ترقية المرحلة ٢ج، خيار A من مقترح تكامل مصادر جديدة).

    تقنية «إحصاءات المرآة» (نفس مبدأ mirror_saudi_export في
    silk_data_layer_v2.py، لكن معمَّمة لكل مورّد لا لسعودية فقط): بدل سؤال
    السوق «كم استوردتِ؟» (قد لا تُبلِغ لكومتريد إطلاقاً — شائع لأسواق نامية
    كثيرة)، اسأل كل الدول الأخرى «كم صدّرتِ لهذه السوق؟» عبر الاتجاه
    المعاكس (comtrade_trade بـreporter='all', partner=السوق, flow معكوس).
    مجموع تصريحات التصدير هذه تقدير بديل، لا تقرير مباشر — يُستدعى فقط
    كـ**احتياط** حين يعيد الاستعلام المباشر [] (سجل حقيقي غائب)، لا عند
    فشل الجلب (شبكة/429 — إعادة المحاولة بمعامل مختلف لن تحلّ عطلاً حياً
    وتستهلك ميزانية كومتريد اليومية بلا داعٍ). None على الفشل/الغياب أيضاً
    — لا صفر مختلَق، ولا اختلاق قيمة حين يعيد نداء المرآة نفسه فراغاً.
    """
    inverse_flow = "X" if flow == "M" else "M"
    recs = comtrade_trade(hs_code, "all", year, flow=inverse_flow,
                          partner=market_m49)
    if not recs:
        return None
    vals = [v for v in (primary_value(r) for r in recs) if v is not None]
    return sum(vals) if vals else None


# نسخ مؤشر الأداء اللوجستي (LPI) للبنك الدولي — تصدر بفواصل، لا سنة بينية
# لها نسخة منشورة (بلاغ Nadec/اليمن #7: كاتب استشهد بـLPI 2022 لا نسخة لها).
# مشترَك مع حارس البوّابة `_LPI_INVALID_EDITION_YEARS`.
_LPI_EDITIONS = (2007, 2010, 2012, 2014, 2016, 2018, 2023)


def _is_lpi_indicator(indicator: str) -> bool:
    return str(indicator or "").upper().startswith("LP.LPI.")


def _snap_lpi_year(year: int) -> int:
    """ثبّت سنةً مطلوبةً على أحدث نسخة LPI منشورة ≤ المطلوبة (وإلا الأحدث
    مطلقاً) — كي لا تُقرَن سنةٌ بينيةٌ بالمؤشر في الملاحظة."""
    eligible = [e for e in _LPI_EDITIONS if e <= year]
    return eligible[-1] if eligible else _LPI_EDITIONS[-1]


def world_bank(iso3: str, indicator: str, year: int | None = None) -> DataPoint:
    """مؤشر البنك الدولي — القيمة لسنة محددة، أو أحدث سنة منشورة.

    بلاغ حي (الموجة ٨): مؤشرات مثل WGI (PV.EST/RL.EST) وLPI تُنشَر بفارق
    سنة أو أكثر أحياناً، وLPI كل سنتين فقط — طلب سنة محددة (كما قد تفعل
    بعثة كلود عبر أداة worldbank_indicator) لم تُنشر بعد كان يعيد فجوة
    صامتة (None) رغم توفر بيانات فعلية حقيقية لسنوات أقرب. الآن: إن فشلت
    السنة المطلوبة تحديداً، تراجُع صريح واحد لأحدث سنة منشورة فعلاً —
    مُعلَن في الملاحظة، لا اختلاق ولا فجوة زائفة.

    عائلة LPI (بلاغ Nadec/اليمن #7): سنةٌ مطلوبةٌ ليست نسخةً منشورة
    (2019-2022/2024) تُثبَّت **قبل الجلب** على أحدث نسخة منشورة — فلا
    تُقرَن سنةٌ بينيةٌ بالمؤشر في الملاحظة (كان الكاتب يستشهد بها فيُطلِق
    `lpi_invalid_edition_year`). القيمة نفسها بيانات حقيقية للنسخة المنشورة."""
    if year is not None and _is_lpi_indicator(indicator) \
            and year not in _LPI_EDITIONS:
        year = _snap_lpi_year(year)
    dp = _world_bank_for_year(iso3, indicator, year)
    if dp.value is not None or year is None:
        return dp
    fallback = _world_bank_for_year(iso3, indicator, None)
    if fallback.value is not None:
        return DataPoint(
            value=fallback.value, source=fallback.source,
            confidence=fallback.confidence,
            note=f"{indicator}: سنة {year} لم تُنشر بعد لـ{iso3} — "
                 f"استُخدمت أحدث سنة متاحة ({fallback.note})",
            retrieved_at=fallback.retrieved_at)
    return dp


# مؤشرات الحوكمة العالمية (WGI) — بلاغ حي (تشغيلة تمور/هولندا الثالثة):
# PV.EST/RL.EST/RQ.EST صارت "مؤرشفة" في قاعدة WDI الافتراضية (source=2،
# ما يخدمه /v2/country/{iso3}/indicator/{code} بلا معامل source) — موطن
# WGI الحالي هو قاعدتها المستقلة source=3؛ الرموز نفسها تعمل هناك.
# التمرير الصريح للمصدر يعيد البيانات الحية بدل خطأ الأرشفة.
_WB_INDICATOR_SOURCE = {
    "PV.EST": "3", "RL.EST": "3", "RQ.EST": "3",
    "GE.EST": "3", "CC.EST": "3", "VA.EST": "3",
}


def _wb_shape_error(payload: object) -> str | None:
    """تحقّق من شكل ردّ البنك الدولي — الموجة ١٠ (بلاغ حي: WGI فارغ لهولندا
    ثم إسبانيا كان يتدهور بصمت لملاحظة عامة عبر except الشامل بدل تشخيص
    واضح). يعيد رسالة خطأ عربية إن كان الشكل مخالفاً لعقد الـAPI الموثَّق
    (`[{page,...}, [records]]` أو `[{"message":[...]}]` عند خطأ)، وإلا None.
    لا يستهلك الشبكة — فحص بنيوي صرف على جسم مُستلَم فعلاً."""
    if not isinstance(payload, list) or not payload:
        return f"شكل ردّ غير متوقع من البنك الدولي: {type(payload).__name__}"
    envelope = payload[0]
    if isinstance(envelope, dict) and envelope.get("message"):
        msgs = envelope["message"]
        detail = (msgs[0].get("value") or msgs[0].get("key") or str(msgs[0])
                  if isinstance(msgs, list) and msgs else str(msgs))
        return f"البنك الدولي أعاد خطأ API: {detail}"
    if len(payload) < 2:
        return "ردّ البنك الدولي بلا صفحة سجلات (عنصر ثانٍ غائب)"
    records = payload[1]
    if records is None:
        return None  # صفحة فارغة صالحة (لا سجلات لهذا المؤشر/الدولة) — ليس خطأ شكل
    if not isinstance(records, list):
        return f"سجلات البنك الدولي ليست قائمة: {type(records).__name__}"
    return None


def _world_bank_for_year(iso3: str, indicator: str,
                         year: int | None) -> DataPoint:
    """جلب فعلي لسنة محددة أو الأحدث — helper مستدعى مباشرة من world_bank()
    ومن مسار التراجُع فيه؛ لا يُستدعى مباشرة خارج هذا الملف.

    الموجة ١٠: تحقّق صريح من شكل الردّ (`_wb_shape_error`) قبل التفسير —
    خطأ API (رمز مؤشر/دولة غير صحيح) أو جسم مشوَّه ينتج ملاحظة تشخيصية
    واضحة بدل السقوط في except الشامل برسالة استثناء غامضة."""
    url = f"{ENDPOINTS['world_bank']}/country/{iso3}/indicator/{indicator}"
    params = {"format": "json", "per_page": "100"}
    src = _WB_INDICATOR_SOURCE.get(indicator)
    if src:  # WGI تعيش في source=3 — راجع تعليق _WB_INDICATOR_SOURCE
        params["source"] = src
    if year is not None:
        params["date"] = str(year)
    try:
        payload = _cached_get(url, params, ttl_seconds=7 * 86400)  # M2: WB 7d
        if payload is None:  # cache miss + fetch failed -> fall back to direct GET
            r = _http_get(url, params)
            r.raise_for_status()
            payload = r.json()
        shape_err = _wb_shape_error(payload)
        if shape_err:
            note = f"{indicator} ({iso3}): {shape_err}"
            log.warning(note)
            return DataPoint(None, "World Bank", 0.0, note, _today())
        records = payload[1] or []
        for rec in records:  # WB returns newest-first; take first non-null
            if isinstance(rec, dict) and rec.get("value") is not None:
                _dy = None
                try:
                    _dy = int(str(rec.get("date"))[:4])
                except (TypeError, ValueError):
                    _dy = None
                # سنة البيانات حقلٌ بنيويّ (data_year) لا وسمٌ نصّيّ «year=»
                # (الدرس ٣٣ + مراجعة الشيفرة): الملاحظة تُبقي السنة بصيغة بشرية
                # مقروءة «(2013)»، والفِنتيج يُقرأ من الحقل لا من النثر.
                return DataPoint(
                    value=rec["value"], source="World Bank", confidence=0.95,
                    note=f"{indicator} ({rec.get('date')})", retrieved_at=_today(),
                    data_year=_dy,
                )
        note = f"{indicator}: no value returned for {iso3}"
        log.warning(note)
        return DataPoint(None, "World Bank", 0.0, note, _today())
    except Exception as e:  # noqa: BLE001
        note = f"{indicator} fetch failed for {iso3}: {e}"
        log.warning(note)
        return DataPoint(None, "World Bank", 0.0, note, _today())


def gdp_per_capita(iso3: str, year: int | None = None) -> DataPoint:
    """نصيب الفرد من الناتج (US$) — GDP per capita, current US$."""
    return world_bank(iso3, "NY.GDP.PCAP.CD", year)


def population(iso3: str, year: int | None = None) -> DataPoint:
    """عدد السكان — total population."""
    return world_bank(iso3, "SP.POP.TOTL", year)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Silk data layer — tiny live probe (degrades gracefully offline)")
    dp = gdp_per_capita("SAU")
    if dp.value is None:
        print(f"  GDP/capita SAU: no data / fetch failed — {dp.note}")
    else:
        print(f"  GDP/capita SAU = {dp.value} US$ [{dp.source}, {dp.note}]")
    recs = comtrade_trade("100630", 840, 2022, flow="M", partner=0)
    if not recs:
        print("  Comtrade rice imports (USA, 2022): no data / fetch failed")
    else:
        print(f"  Comtrade returned {len(recs)} record(s); "
              f"first partner = {recs[0].get('partnerName')}")
