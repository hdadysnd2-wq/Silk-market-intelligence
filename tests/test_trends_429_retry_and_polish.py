"""بلاغ إنتاجي (Nadec/اليمن، التحليل #7): بعثة demand_trends فشلت مراراً على
HTTP 429 من Google Trends بلا أي إعادة محاولة — نداء pytrends الواحد كان يسقط
مباشرة إلى فجوة معلنة، وأداة البعثة كانت تتجاوز سلسلة الصمود (اللقطة المخزَّنة).

الأقفال (كلها test-first — كُتبت قبل الفكس وفشلت على الشيفرة القديمة):

1. `_retry_429`: إعادة محاولة عند 429 فقط بتراجع أُسّي (SILK_TRENDS_RETRIES/
   SILK_TRENDS_BACKOFF_S)؛ استثناء غير 429 يمرّ فوراً بلا نوم.
2. أداة البعثة `_tool_trends_interest` تستدعي `trends_interest_resilient`
   (سلسلة WS11.1: حيّ ← لقطة مخزَّنة ← فجوة) لا `trends_interest` العارية.
3. HHI على مقياس 0-10000 رقم صحيح لا عشري (بوابة `hhi_false_precision`).
4. سقف ملحق التدقيق يستوعب 102 استشهاداً (بلاغ `audit_coverage`) — ثابت
   واحد مشترك بين البوابة والمصدِّر.
5. «خطأ (HTTP 429):» لا تتضاعف إلى «خطأ (خطأ استجابة الخادم (HTTP 429)):»
   — والتعريب idempotent (تطبيقه مرتين لا يغيّر شيئاً).

Run: python3 -m pytest tests/test_trends_429_retry_and_polish.py -q
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TooManyRequestsError(Exception):
    """اسم الصنف يطابق عائلة pytrends.exceptions.TooManyRequestsError."""


def _df(kw: str) -> pd.DataFrame:
    idx = pd.date_range("2025-08-01", periods=12, freq="MS")
    return pd.DataFrame({kw: [10, 20, 30, 40, 50, 60, 70, 80, 90, 80, 70, 60]},
                        index=idx)


def _fake_trendreq(fail_first: int, exc: Exception | None = None):
    """صانع TrendReq مموّه — يفشل أول `fail_first` جلبة ثم ينجح."""
    state = {"calls": 0}

    class _Fake:
        def __init__(self, *a, **k):
            self.kw = None

        def build_payload(self, kw_list, timeframe=None, geo=None):
            self.kw = kw_list[0]

        def interest_over_time(self):
            state["calls"] += 1
            if state["calls"] <= fail_first:
                raise exc or TooManyRequestsError(
                    "The request failed: Google returned a response with code 429")
            return _df(self.kw)

    return _Fake, state


# ── ١) إعادة المحاولة عند 429 بتراجع أُسّي ─────────────────────────────────

def test_trends_interest_retries_429_with_exponential_backoff(monkeypatch):
    import silk_trends_agent as T
    sleeps: list[float] = []
    monkeypatch.setattr(T, "_sleep", sleeps.append)
    fake, state = _fake_trendreq(fail_first=2)
    with mock.patch("pytrends.request.TrendReq", fake):
        dp = T.trends_interest("زبدة الفول السوداني", geo="YE")
    assert dp.value is not None            # نجحت المحاولة الثالثة
    assert state["calls"] == 3
    assert sleeps == [2.0, 4.0]            # تراجع أُسّي: 2 ثم 4


def test_trends_interest_429_exhausted_declares_gap(monkeypatch):
    import silk_trends_agent as T
    sleeps: list[float] = []
    monkeypatch.setattr(T, "_sleep", sleeps.append)
    fake, state = _fake_trendreq(fail_first=99)
    with mock.patch("pytrends.request.TrendReq", fake):
        dp = T.trends_interest("زبدة الفول السوداني", geo="YE")
    assert dp.value is None and dp.confidence == 0.0   # فجوة معلنة، لا اختلاق
    assert "429" in (dp.note or "")
    assert state["calls"] == 3             # الافتراضي: محاولتان إضافيتان
    assert len(sleeps) == 2


def test_non_429_error_does_not_retry(monkeypatch):
    import silk_trends_agent as T
    sleeps: list[float] = []
    monkeypatch.setattr(T, "_sleep", sleeps.append)
    fake, state = _fake_trendreq(fail_first=99, exc=RuntimeError("boom"))
    with mock.patch("pytrends.request.TrendReq", fake):
        dp = T.trends_interest("تمر", geo="YE")
    assert dp.value is None
    assert state["calls"] == 1 and sleeps == []   # لا إعادة لغير 429


def test_retry_config_env(monkeypatch):
    import silk_trends_agent as T
    monkeypatch.setenv("SILK_TRENDS_RETRIES", "0")
    sleeps: list[float] = []
    monkeypatch.setattr(T, "_sleep", sleeps.append)
    fake, state = _fake_trendreq(fail_first=99)
    with mock.patch("pytrends.request.TrendReq", fake):
        dp = T.trends_interest("تمر", geo="YE")
    assert dp.value is None and state["calls"] == 1 and sleeps == []


def test_all_pytrends_fetch_sites_route_through_retry():
    """كل مواضع جلب pytrends الأربعة (interest/series/context/seasonality)
    تمرّ عبر `_retry_429` — فحص مصدري كي لا يُنسى موضع عند إضافة رابع."""
    src = open(os.path.join(_ROOT, "silk_trends_agent.py"),
               encoding="utf-8").read()
    assert src.count("_retry_429(") >= 4


# ── ٢) أداة البعثة تستعمل سلسلة الصمود (لقطة مخزَّنة عند فشل الحيّ) ────────

def test_mission_trends_tool_uses_resilient_chain():
    src = open(os.path.join(_ROOT, "silk_llm_runtime.py"),
               encoding="utf-8").read()
    body = src.split("def _tool_trends_interest(")[1].split("\ndef ")[0]
    assert "trends_interest_resilient" in body


# ── ٣) HHI رقم صحيح على مقياس 0-10000 ──────────────────────────────────────

def test_hhi_is_integer_on_10000_scale():
    from silk_data_layer import DataPoint
    import silk_llm_runtime as R
    import silk_data_layer_v2 as V2
    comps = [
        DataPoint({"partner": "الإمارات", "share": 48.3}, "UN Comtrade",
                  0.9, "x", "2026-07-29"),
        DataPoint({"partner": "السعودية", "share": 33.3}, "UN Comtrade",
                  0.9, "x", "2026-07-29"),
    ]
    ctx = {"hs_code": "040120",
           "market": SimpleNamespace(m49="887", name_en="Yemen", iso2="YE",
                                     iso3="YEM")}
    with mock.patch.object(V2, "market_competitors", lambda *a, **k: comps):
        out = R._tool_comtrade_competitors({"year": 2024}, ctx)
    hhi = out[0].value["hhi"]
    assert isinstance(hhi, int)            # لا 3441.8 — وهم دقّة
    assert hhi == 3442
    assert f"HHI={hhi}" in out[0].note
    # بوابة الجودة لا ترصد دقّة زائفة في الصياغة الناتجة
    from silk_quality_gate import _check_hhi_false_precision
    assert _check_hhi_false_precision(out[0].note) == []


# ── ٤) سقف ملحق التدقيق يستوعب 102 ويبقى ثابتاً واحداً مشتركاً ─────────────

def test_audit_cap_covers_102_citations():
    from silk_quality_gate import _check_audit_coverage, AUDIT_APPENDIX_CAP
    dr = {"missions": {f"m{i}": {"findings": [{}] * 17} for i in range(6)}}
    assert sum(len(m["findings"]) for m in dr["missions"].values()) == 102
    assert _check_audit_coverage(dr) == []          # 102 ≤ السقف الجديد
    over = {"missions": {"m": {"findings": [{}] * (AUDIT_APPENDIX_CAP + 1)}}}
    assert _check_audit_coverage(over)              # التجاوز يبقى معلَناً


def test_audit_cap_single_shared_constant():
    """المصدِّر لا يقصّ على 80 حرفياً — يستورد الثابت نفسه من البوابة."""
    src = open(os.path.join(_ROOT, "silk_reports.py"), encoding="utf-8").read()
    assert "rows[:80]" not in src
    assert "AUDIT_APPENDIX_CAP" in src


# ── ٥) لا بادئة خطأ مزدوجة ولا ترقيم فارغ — والتعريب idempotent ────────────

def test_humanize_collapses_error_http_wrapper():
    from silk_narrative import humanize_technical_note as H
    out = H("خطأ (HTTP 429): Too Many Requests")
    assert "خطأ (خطأ" not in out
    assert out.startswith("خطأ استجابة الخادم (HTTP 429)")
    assert "()" not in out and "( )" not in out


def test_humanize_collapses_already_doubled_stored_note():
    from silk_narrative import humanize_technical_note as H
    for stored in ("خطأ (خطأ استجابة الخادم (HTTP 429)):",
                   "خطأ (خطأ استجابة الخادم (HTTP 429): ):"):
        out = H(stored + " تجاوز حدّ المعدّل")
        assert "خطأ (خطأ" not in out
        assert out.startswith("خطأ استجابة الخادم (HTTP 429): ")
        assert ": )" not in out and "():" not in out


def test_humanize_http_status_idempotent():
    from silk_narrative import humanize_technical_note as H
    once = H("pytrends unavailable / no network: HTTP 429: quota")
    assert "خطأ استجابة الخادم (HTTP 429)" in once
    assert H(once) == once                 # تطبيق ثانٍ لا يضاعف البادئة
