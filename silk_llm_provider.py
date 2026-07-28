"""محوّل مزوّد كلود الرقيق — thin LLM provider seam (تدقيق المعمارية، دين ٣).

مشكلة قبل هذا الملف: `silk_ai_judge._call`/`_call_tools` كانا يعرفان تفاصيل
Anthropic HTTP مباشرة (المسار، رأس الإصدار، شكل الحمولة) — أي مزوّد بديل
مستقبلاً (OpenAI مثلاً) يعني جراحة في كل موضع نداء. هذا الملف يستخرج تلك
التفاصيل خلف واجهة `LLMProvider` بمنهجين فقط: `complete` (نداء نص مفرد) و
`complete_tools` (حلقة استخدام أدوات متعددة الأدوار) — تماماً كما كان
`_call`/`_call_tools` يفعلان، بلا أي تغيير سلوكي (نفس المسار، نفس الحمول،
نفس معالجة الفشل/الرفض).

لا مزوّد ثانٍ اليوم — Anthropic هو التنفيذ الوحيد؛ الاختيار عبر إعداد
(`SILK_LLM_PROVIDER`, افتراضي "anthropic") بدل استيراد مباشر، فإضافة مزوّد
لاحقاً = صفّ جديد + سطر تسجيل في `_PROVIDERS`، لا تغيير في مواضع النداء.
"""
from __future__ import annotations

import contextvars
import logging
import os
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)

# آخر تفصيل فشل نداء كلود — بلاغ حي إنتاجي (ثالث تشغيلة، كاتب التقرير):
# None عائد من complete/complete_tools كان يعني "مفتاح غائب أو فشل" بلا
# أي وسيلة لمعرفة نوع الفشل الفعلي (Timeout؟ خطأ شبكة؟ رفض HTTP؟) سوى
# البحث يدوياً في سجلات الخادم. contextvar يُضبط عند كل نداء (نجاحاً كان
# أو فشلاً) فيُقرأ فوراً بعد النداء — آمن مع ThreadPoolExecutor (نفس نمط
# silk_context، عبر copy_context() لا حالة عالمية مشتركة بين الخيوط).
_last_error: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "silk_llm_last_error", default=None)


def last_error() -> dict | None:
    """آخر تفصيل فشل نداء كلود في هذا السياق — {"type","message"} أو None
    (لا نداء بعد، أو آخر نداء نجح). اقرأها فوراً بعد نداء أعاد None لمعرفة
    السبب الفعلي بدل التخمين."""
    return _last_error.get()


# سبب توقّف آخر نداء — بلاغ حي إنتاجي (كاتب التقرير، تمور/هولندا HS080410):
# رد ناجح بـstop_reason="max_tokens" (نص مقتطع أو بلا نص) كان يعيد None
# فيصير report=None (سلسلة PRs #69/#70/#71). المزوّد طبقة HTTP رقيقة لا تعرف
# التتبّع؛ فبدل حلقة تصعيد مخفية داخله، يعرض `stop_reason` لطبقة الكاتب
# (silk_ai_judge) التي تصعّد السقف وتعيد المحاولة — **كل محاولة نداءٌ مُتتبَّع
# مستقل** (report_call event + عدّ llm_calls + قياس رموز)، لا حلقة صامتة
# خارج طبقة التتبّع/العدّ. contextvar يُضبط عند كل نداء ويُقرأ فوراً بعده.
_last_stop_reason: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "silk_llm_last_stop_reason", default=None)


def last_stop_reason() -> str | None:
    """سبب توقّف آخر نداء `complete` في هذا السياق ("max_tokens"/"end_turn"/…)
    أو None (لا نداء نصّي بعد، أو فشل قبل الرد). تقرأه طبقة الكاتب لتقرّر
    تصعيد سقف الإخراج (نص مقتطع) بدل تخمين."""
    return _last_stop_reason.get()


# ── temperature: قرار واعٍ بالنموذج · model-aware temperature ────────────────
# الصفّ ٤٧ في docs/LESSONS.md ثبّت `temperature=0` على كل نداءات `complete`
# لأن الافتراضي (1.0) أنتج حكمين مختلفين لنفس المدخلات في يوم واحد (WATCH ثم
# GO). **القانون قائم ولا يُمَسّ** — الحتمية تبقى مطلوبة حيث يقبلها المزوّد.
#
# لكن Anthropic أزالت معاملات المعاينة (`temperature`/`top_p`/`top_k`) من عائلة
# نماذج الجيل الخامس: إرسالها يردّ **400 invalid_request_error**، فيصير كل نداء
# توليف/كاتب/مراجع/رؤية فشلاً كاملاً (`complete` → None) — أي تقرير بلا سرد، لا
# مجرّد حكم غير حتمي. الحذف الشامل يعيد اللاحتمية على النماذج التي تقبلها؛
# لذا القرار **لكل نموذج**: تُرسَل حيث تُقبَل، وتُهمَل حيث تُرفَض.
#
# `SILK_LLM_NO_TEMPERATURE` مخرج تشغيلي بلا نشر: قائمة بادئات مفصولة بفواصل
# (أو مسافات) تحلّ محل الافتراضية بالكامل — تُضاف بادئة جديدة حين يُرقّى نموذج
# آخر، أو تُفرَّغ («») لإعادة إرسال temperature لكل النماذج.
_NO_TEMPERATURE_PREFIXES: tuple[str, ...] = (
    "claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-mythos-5")


def _no_temperature_prefixes() -> tuple[str, ...]:
    """بادئات النماذج التي تُهمَل معها `temperature` — الافتراضية أو ما يضبطه
    `SILK_LLM_NO_TEMPERATURE`. المتغيّر **مضبوطاً** يحلّ محل الافتراضية كاملةً
    (قيمة فارغة صريحة = لا نموذج يُستثنى)؛ **غير مضبوط** = الافتراضية."""
    raw = os.environ.get("SILK_LLM_NO_TEMPERATURE")
    if raw is None:
        return _NO_TEMPERATURE_PREFIXES
    return tuple(p for p in raw.replace(",", " ").split() if p)


def _supports_temperature(model: str) -> bool:
    """هل يقبل هذا النموذج معامل `temperature`؟ (المطابقة ببادئة، بلا حساسية
    حالة الأحرف — فتغطي اللواحق المؤرَّخة مثل `claude-opus-5-20260401`.)"""
    m = (model or "").strip().lower()
    return not any(m.startswith(p.lower().strip())
                   for p in _no_temperature_prefixes())


class LLMProvider(ABC):
    """الواجهة الدنيا — نداء إكمال نصّي، ونداء حلقة استخدام أدوات."""

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int,
                model: str, timeout: float) -> str | None:
        """نص الرد أو None عند غياب مفتاح/فشل/رفض — لا استثناء يتسرّب للمستدعي."""

    @abstractmethod
    def complete_tools(self, system: str, messages: list, tools: list | None,
                       max_tokens: int, model: str, timeout: float) -> dict | None:
        """رد الـMessages API الخام (غير مُحلَّل) أو None — يقود `silk_llm_runtime`
        حلقة tool_use/tool_result فوقه."""

    def complete_vision(self, system: str, text: str, image_b64: str,
                        media_type: str, max_tokens: int, model: str,
                        timeout: float) -> str | None:
        """نداء رؤية واحد (صورة + نصّ) — نص الرد أو None عند غياب مفتاح/فشل/رفض.

        اختياري (تنفيذ افتراضي = None) كي لا يُلزَم كل مزوّد به؛ يُستعمله مسار
        استقبال المنتج المتعدد الوسائط (بطاقة مكوّنات/صورة منتج). لا استثناء
        يتسرّب للمستدعي — فشلٌ = None => «تعذّرت القراءة» لا اختلاق."""
        return None


class AnthropicProvider(LLMProvider):
    """التنفيذ الوحيد اليوم — يغلّف api.anthropic.com/v1/messages حرفياً
    كما كان `silk_ai_judge._call`/`_call_tools` يفعلان قبل هذا الاستخراج."""

    _ENDPOINT = "https://api.anthropic.com/v1/messages"
    _VERSION = "2023-06-01"

    def __init__(self, api_key_env: str = "ANTHROPIC_API_KEY") -> None:
        self._api_key_env = api_key_env

    def _key(self) -> str:
        return os.environ.get(self._api_key_env, "").strip()

    def _headers(self, key: str) -> dict:
        return {"x-api-key": key, "anthropic-version": self._VERSION,
                "content-type": "application/json"}

    @staticmethod
    def _record_usage(model: str, data: dict) -> None:
        """سجّل رموز الرد في عدّاد اقتصاد البيانات — قناة جانبية صامتة (دين ٤)،
        لا تغيّر عقد complete/complete_tools؛ no-op خارج تحليل نشط أو بلا usage.

        `cache_read_input_tokens`/`cache_creation_input_tokens` (Prompt
        Caching، المرحلة ٠): حقلا usage إضافيان من Anthropic حين يُخزَّن
        `system`/`tools` — غيابهما (نداء بلا كاش) يمرّر صفراً بلا أثر."""
        usage = data.get("usage") if isinstance(data, dict) else None
        if not usage:
            return
        import silk_context  # lazy: keep this module cycle-safe and offline
        silk_context.record_llm_usage(
            model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_creation_tokens=usage.get("cache_creation_input_tokens", 0))

    @staticmethod
    def _timeout_pair(timeout: float) -> tuple[float, float]:
        """(مهلة اتصال، مهلة قراءة) — بلاغ حي (ثالث تشغيلة، كاتب التقرير):
        قيمة مفردة تُطبَّق كمهلتَي اتصال وقراءة معاً في requests؛ اتصال
        TCP بـapi.anthropic.com لا يجب أن يستغرق قرب المهلة الكاملة أبداً
        — فصلهما يُفشل مشاكل الاتصال (DNS/شبكة) خلال ثوانٍ بدل انتظار
        المهلة الكاملة، ولا يغيّر سلوك التوليد الطويل المشروع (مهلة
        القراءة تبقى كاملة). تمييز الاستثناء الناتج (ConnectTimeout مقابل
        ReadTimeout) يوضّح تلقائياً أيّ طَوري الفشل وقع — بلا تخمين."""
        return (min(10.0, timeout), timeout)

    # الحالات العابرة التي تُعاد محاولتها فقط — 429 (تجاوز حصّة/معدّل) و529
    # (ازدحام Anthropic). كلاهما يُرفض **قبل** التوليد فكلفة الرموز صفر، وإعادة
    # المحاولة بـbackoff هي النمط الموصى به. غير هذين (400 حمولة/401 مفتاح/رفض)
    # لا يُعاد — فشل فوري كما كان.
    _RETRYABLE_STATUS = (429, 529)

    # أخطاء الاتصال السريعة العابرة — بلاغ مالك حيّ بدليل مباشر (تقرير الكويت،
    # تشغيلتان): نداء الكاتب (أثقل حمولة، في الذيل) فشل بـ«تعذّر الاتصال بالمصدر»
    # بينما المحلّل نجح على نفس الخادم قبله بثوانٍ — فشلٌ متقطّع في طور الاتصال،
    # لا حصّة (لم يلتقطه retry الـ429/529) ولا مهلة قراءة بطيئة (المدّة 200ث <
    # 300ث). ConnectTimeout يفشل ≤10ث (راجع _timeout_pair) فكلفة الرموز صفر،
    # وإعادة محاولته آمنة. **ReadTimeout مستثنى عمداً**: يعني أن النموذج ولّد
    # فعلاً حتى مهلة القراءة الكاملة — إعادته تحرق رموزاً/وقتاً بلا طائل، وذاك
    # مسار إصلاح مختلف (تقليص الحمولة/streaming) يقرّره دليل writer-diagnostics.
    @classmethod
    def _is_retryable_exc(cls, exc: Exception) -> bool:
        """هل الاستثناء خطأ اتصال سريع عابر يُعاد؟ ConnectTimeout/ConnectionError
        نعم؛ ReadTimeout (مهلة قراءة بطيئة) وغيرها لا."""
        try:
            import requests
        except Exception:  # noqa: BLE001
            return False
        exc_type = type(exc)
        # ReadTimeout يرث Timeout؛ نستثنيه صراحةً قبل فحص العائلة العابرة.
        if isinstance(exc, requests.exceptions.ReadTimeout):
            return False
        return isinstance(exc, (requests.exceptions.ConnectTimeout,
                                requests.exceptions.ConnectionError))

    @staticmethod
    def _retry_after(resp, cap: float = 60.0) -> float | None:
        """ثوانٍ من ترويسة Retry-After إن وُجدت (Anthropic يرسلها على 429) —
        مقيّدة بـcap كي لا ننام دهراً. None حين غيابها/تعذّر تحليلها."""
        val = None
        try:
            val = (resp.headers or {}).get("retry-after")
        except Exception:  # noqa: BLE001 — ترويسة اختيارية
            return None
        if not val:
            return None
        try:
            return max(0.0, min(cap, float(val)))
        except (TypeError, ValueError):
            return None

    def _post(self, key: str, payload: dict, timeout: float):
        """POST واحد مع إعادة محاولة + backoff أسّي على الحالات العابرة فقط.

        يُرجع `resp` (بعد raise_for_status) أو يرمي آخر استثناء — فيلتقطه
        المستدعي و`_error_detail` كما اليوم (فبعد استنفاد المحاولات، السلوك
        مطابق للسابق تماماً: None + خطأ مُسجَّل). SILK_LLM_MAX_RETRIES=0 يعطّل
        الإعادة (سلوك اليوم بالضبط). العدّاد المالي يُحجَز مرّة قبل التشغيلة لا
        لكل محاولة HTTP، و429/529 صفر رموز — فالكلفة ~صفر."""
        import time
        import requests  # lazy: keep core import offline-safe
        try:
            max_retries = max(0, int(os.environ.get(
                "SILK_LLM_MAX_RETRIES", "2").strip() or "2"))
        except ValueError:
            max_retries = 2
        try:
            base = max(0.0, float(os.environ.get(
                "SILK_LLM_RETRY_BASE_S", "1.0").strip() or "1.0"))
        except ValueError:
            base = 1.0
        attempt = 0
        while True:
            try:
                resp = requests.post(
                    self._ENDPOINT, timeout=self._timeout_pair(timeout),
                    headers=self._headers(key), json=payload)
            except Exception as exc:  # noqa: BLE001
                # خطأ اتصال سريع عابر (ConnectTimeout/ConnectionError) → أعِد
                # المحاولة؛ غيره (ReadTimeout/…) يُرمى فوراً للمستدعي كالسابق.
                if self._is_retryable_exc(exc) and attempt < max_retries:
                    wait = base * (2 ** attempt)
                    log.warning("AI call transient %s (attempt %d/%d) — retry "
                                "in %.1fs", type(exc).__name__, attempt + 1,
                                max_retries + 1, wait)
                    if wait > 0:
                        time.sleep(wait)
                    attempt += 1
                    continue
                raise
            # getattr دفاعي: كائن ردٍّ بلا status_code (نادر) يُعامَل كغير عابر
            # فيمرّ لـraise_for_status كالسابق — لا انهيار على شكل ردٍّ غريب.
            if (getattr(resp, "status_code", None) in self._RETRYABLE_STATUS
                    and attempt < max_retries):
                wait = self._retry_after(resp)
                if wait is None:
                    wait = base * (2 ** attempt)
                log.warning("AI call transient %s (attempt %d/%d) — retry in "
                            "%.1fs", resp.status_code, attempt + 1,
                            max_retries + 1, wait)
                if wait > 0:
                    time.sleep(wait)
                attempt += 1
                continue
            resp.raise_for_status()
            return resp

    def complete(self, system, user, max_tokens, model, timeout):
        _last_error.set(None)       # نظافة الحالة من أول سطر — لا تسريب بين نداءات
        _last_stop_reason.set(None)
        key = self._key()
        if not key:
            return None
        try:
            # WP-1 §1: temperature مثبّتة صفراً على كل نداءات `complete`
            # (التوليف/الكاتب/المراجع/المحلل النصي) — الافتراضي 1.0 أنتج
            # حكمين مختلفين لنفس المدخلات في يوم واحد (WATCH ثم GO). لا top_p
            # معها — Anthropic توصي بضبط أحدهما لا كليهما. حلقة الأدوات
            # (`complete_tools`) تبقى على افتراضها: مخرجاتها لا تحدّد الحكم
            # المعروض مباشرة (الحكم من المحرّك الحتمي).
            # تُهمَل — لا تُحذَف — على النماذج التي ترفضها بـ400 (انظر
            # `_supports_temperature` أعلاه): إرسالها هناك يقتل النداء كلّه.
            payload = {"model": model, "max_tokens": max_tokens,
                       "system": [{"type": "text", "text": system,
                                   "cache_control": {"type": "ephemeral"}}],
                       "messages": [{"role": "user", "content": user}]}
            if _supports_temperature(model):
                payload["temperature"] = 0
            resp = self._post(key, payload, timeout)
            data = resp.json()
            self._record_usage(model, data)
            stop_reason = data.get("stop_reason")
            _last_stop_reason.set(stop_reason)   # تقرؤه طبقة الكاتب للتصعيد
            if stop_reason == "refusal":  # safety decline -> no fabrication
                log.warning("AI judge: request refused by the model")
                _last_error.set({"type": "refusal",
                                 "message": "model refused the request"})
                return None
            text = "".join(b.get("text", "") for b in data.get("content", [])
                          if b.get("type") == "text").strip()
            if not text:
                # رد HTTP ناجح بلا كتل نصية (stop_reason=max_tokens نموذجياً) —
                # يُعلَن فجوة صريحة، لا اختلاق. `last_stop_reason` مضبوط أعلاه
                # فتعرف طبقة الكاتب أن السبب اقتطاعٌ وتصعّد (بلاغ هولندا).
                detail = {"type": "empty_response",
                          "message": f"HTTP 200 بلا كتل نصية — "
                                     f"stop_reason={stop_reason!r}"}
                log.warning("AI judge call returned no text: %s", detail["message"])
                _last_error.set(detail)
                return None
            _last_error.set(None)
            # نص مقتطع (stop_reason=max_tokens) يُعاد كما هو؛ طبقة الكاتب تقرّر
            # التصعيد عبر last_stop_reason() — نص جزئي مفيد لا None.
            return text
        except Exception as e:  # noqa: BLE001 — optional layer must never crash analysis
            # سجّل رمزَ الحالة وجسمَ الردّ الفعليَّين لا نوعَ الاستثناء وحده
            # (طلب المالك): «تعذّر الاتصال» وحدها لا تُميّز 429 من 401 من 529،
            # وهي أفعالٌ تشغيليةٌ مختلفة. `_error_detail` يلتقطهما أصلاً —
            # كان السجلّ يرميهما فيبقى التشخيص مستحيلاً من السجلّات.
            detail = self._error_detail(e)
            log.warning("AI judge call failed: %s: %s (status=%s body=%s)",
                        detail.get("type"), detail.get("message"),
                        detail.get("status_code"), detail.get("response_body"))
            _last_error.set(detail)
            return None

    def complete_tools(self, system, messages, tools, max_tokens, model, timeout):
        _last_error.set(None)  # نظافة الحالة من أول سطر — لا تسريب بين نداءات
        key = self._key()
        if not key:
            return None
        try:
            payload = {"model": model, "max_tokens": max_tokens,
                      "system": [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}],
                      "messages": messages}
            if tools:
                # علّم آخر أداة فقط — Anthropic يخزّن كل ما قبل نقطة التعليم
                # (system + tools معاً) ككتلة كاش واحدة مستقرة عبر جولات
                # الحلقة، إذ لا يتغيّر تعريف الأدوات بين الجولات.
                payload["tools"] = [*tools[:-1],
                                    {**tools[-1],
                                     "cache_control": {"type": "ephemeral"}}]
            resp = self._post(key, payload, timeout)
            data = resp.json()
            self._record_usage(model, data)
            _last_error.set(None)
            return data
        except Exception as e:  # noqa: BLE001 — optional layer must never crash analysis
            log.warning("AI tool call failed: %s: %s", type(e).__name__, e)
            _last_error.set(self._error_detail(e))
            return None

    def complete_vision(self, system, text, image_b64, media_type,
                        max_tokens, model, timeout):
        """نداء رؤية واحد — صورة base64 + نصّ في رسالة مستخدم واحدة.

        يعكس `complete` حرفياً لكن المحتوى قائمةُ كتلٍ (صورة ثم نص). يسجّل
        الرموز في عدّاد اقتصاد البيانات (قناة جانبية) فتُحتسب كلفتُه كأيّ
        نداء. غياب المفتاح/الرفض/الفشل => None (فجوة معلنة، لا اختلاق)."""
        _last_error.set(None)
        _last_stop_reason.set(None)
        key = self._key()
        if not key:
            return None
        try:
            # WP-1 §1: استخلاص حتمي كالنصّي — ومُهمَلة على النماذج الرافضة
            # (نفس قرار `complete`؛ `_supports_temperature` هو الحكم الواحد).
            payload = {"model": model, "max_tokens": max_tokens,
                       "system": [{"type": "text", "text": system,
                                   "cache_control": {"type": "ephemeral"}}],
                       "messages": [{"role": "user", "content": [
                           {"type": "image",
                            "source": {"type": "base64",
                                       "media_type": media_type,
                                       "data": image_b64}},
                           {"type": "text", "text": text}]}]}
            if _supports_temperature(model):
                payload["temperature"] = 0
            resp = self._post(key, payload, timeout)
            data = resp.json()
            self._record_usage(model, data)
            stop_reason = data.get("stop_reason")
            _last_stop_reason.set(stop_reason)
            if stop_reason == "refusal":
                log.warning("vision call refused by the model")
                _last_error.set({"type": "refusal",
                                 "message": "model refused the request"})
                return None
            out = "".join(b.get("text", "") for b in data.get("content", [])
                          if b.get("type") == "text").strip()
            if not out:
                _last_error.set({"type": "empty_response",
                                 "message": f"HTTP 200 بلا نص — "
                                            f"stop_reason={stop_reason!r}"})
                return None
            _last_error.set(None)
            return out
        except Exception as e:  # noqa: BLE001 — optional layer must never crash
            log.warning("vision call failed: %s: %s", type(e).__name__, e)
            _last_error.set(self._error_detail(e))
            return None

    @staticmethod
    def _error_detail(e: Exception) -> dict:
        """فصّل الاستثناء — بلاغ حي: "مهلة أو خطأ شبكة" الغامضة كانت تخفي
        نوع الفشل الفعلي. لطلب HTTP فاشل (raise_for_status) نُظهر الرد
        (حالة + مقتطف نص) كما طلب المالك صراحة؛ لغيره نوع الاستثناء
        ورسالته (يميّز requests.ConnectTimeout عن requests.ReadTimeout
        تلقائياً — راجع _timeout_pair)."""
        detail = {"type": type(e).__name__, "message": str(e)[:300]}
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail["status_code"] = resp.status_code
                detail["response_body"] = (resp.text or "")[:300]
            except Exception:  # noqa: BLE001 — تفصيل إضافي، لا شرط
                pass
        return detail


_PROVIDERS = {"anthropic": AnthropicProvider}
_provider_instance: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """اختر المزوّد حسب `SILK_LLM_PROVIDER` (افتراضي anthropic) — مفرد
    مُخزَّن مؤقتاً (lazy singleton)؛ اسم غير معروف يتراجع بأمان لـAnthropic."""
    global _provider_instance
    if _provider_instance is None:
        name = os.environ.get("SILK_LLM_PROVIDER", "anthropic").strip().lower()
        cls = _PROVIDERS.get(name, AnthropicProvider)
        _provider_instance = cls()
    return _provider_instance


def reset_provider() -> None:
    """أعد ضبط المفرد المخزَّن — test-only reset (تبديل SILK_LLM_PROVIDER بين
    الاختبارات يتطلبه)."""
    global _provider_instance
    _provider_instance = None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = get_provider()
    print(f"active provider: {type(p).__name__}")
