"""فرقُ رمزَي HS لنفس المنتج والسوق — side-by-side delta between two HS codes.

**لماذا:** حين يتبيّن أنّ تقريراً بُني على رمزٍ خاطئ، السؤالُ التالي دائماً
«وكم غيّر ذلك فعلاً؟». هذه الأداة تُجيبه بأرقامٍ مرصودة: تُشغّل نفسَ مسار
الترتيب على الرمزين وتطبع الفرق في الحقول الأربعة التي تحمل القرار — الحكم،
حجم السوق، تركّز الموردين (HHI)، وحصة السعودية.

**لا اختلاق (المبدأ التأسيسي):** كلُّ حقلٍ يُقرأ من `DataPoint` حقيقيّ. تعذُّر
الجلب يُطبَع «تعذّر الجلب» و«لا سجل» يُطبَع «لا بيانات» — **لا صفرَ بديلاً
ولا رقمَ مُقدَّراً**، ولا فرقٌ يُحسَب على طرفٍ غائب.

Usage:
    python3 tools/hs_delta.py --market YEM --year 2023 040110 040120
    python3 tools/hs_delta.py --market ESP 080410 080420 --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GAP = "—"


def _fmt(v, unit: str = "") -> str:
    if v is None:
        return GAP
    if isinstance(v, float):
        return f"{v:,.2f}{unit}" if abs(v) < 1000 else f"{v:,.0f}{unit}"
    return f"{v:,}{unit}" if isinstance(v, int) else f"{v}{unit}"


def probe(hs_code: str, iso3: str, year: int) -> dict:
    """اقرأ الحقولَ الأربعة لرمزٍ واحد — قراءةٌ حيّة عبر نفس مسار المحرّك."""
    from silk_data_layer import ISO3_TO_M49
    from silk_data_layer_v2 import market_imports
    from silk_hs_confirm import confirm_hs

    m49 = ISO3_TO_M49.get(iso3.upper())
    out: dict = {"hs_code": hs_code, "iso3": iso3.upper(), "year": year,
                 "market_size_usd": None, "hhi": None, "saudi_share_pct": None,
                 "status": "no_record", "code_desc": None}
    if not m49:
        out["status"] = "unknown_market"
        return out
    mi = market_imports(hs_code, m49, year)
    if mi.get("fetch_failed"):
        out["status"] = "fetch_failed"     # تعذّر الجلب ≠ لا سجل (1b)
        return out
    comps = mi.get("competitors") or []
    out["market_size_usd"] = mi.get("total_usd")
    if comps:
        shares = [c.value.get("share") for c in comps
                  if c.value and c.value.get("share") is not None]
        if shares:
            out["hhi"] = round(sum((s / 100.0) ** 2 for s in shares), 4)
        sa = next((c for c in comps
                   if c.value and str(c.value.get("code")) == "682"), None)
        # حصةُ السعودية: مرصودةٌ إن ظهرت، وإلا **غيابٌ مستنتَج** يُعلَن كذلك
        # صراحةً — لا يُقدَّم صفراً مرصوداً (قائمةُ الشركاء قد تكون مبتورة).
        out["saudi_share_pct"] = (sa.value.get("share") if sa else None)
        out["saudi_observed"] = sa is not None
        out["status"] = "ok"
    elif out["market_size_usd"] is not None:
        out["status"] = "ok"
    try:
        out["code_desc"] = confirm_hs("", hs_code).get("code_desc")
    except Exception:  # noqa: BLE001
        pass
    return out


def verdict_for(hs_code: str, iso3: str, year: int, product: str) -> str | None:
    """حكمُ اللجنة الحتمية لهذا الرمز/السوق — المرحلة ١ وحدها (بلا كلود/تكلفة)."""
    try:
        from silk_agents import JuryCommittee, ResearchManager
        from silk_data_layer import ISO3_TO_M49
        from silk_render import _VERDICT_LABELS_AR, _verdict_tone
        m49 = ISO3_TO_M49.get(iso3.upper())
        reports = ResearchManager().distribute(
            {"hs_code": hs_code, "market_m49": m49, "iso3": iso3.upper(),
             "year": year})
        raw = JuryCommittee.evaluate(reports)["verdict"]
        return _VERDICT_LABELS_AR[_verdict_tone(raw)]
    except Exception as e:  # noqa: BLE001 — الحكمُ سطرٌ في جدول، لا يُسقِط الأداة
        return f"{GAP} ({type(e).__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs=2, help="رمزا HS للمقارنة")
    ap.add_argument("--market", required=True, help="ISO3 للسوق")
    ap.add_argument("--year", type=int, default=2023)
    ap.add_argument("--product", default="", help="اسم المنتج (لسطر الحكم)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    rows = [probe(c, a.market, a.year) for c in a.codes]
    for r in rows:
        r["verdict"] = verdict_for(r["hs_code"], a.market, a.year, a.product)

    if a.json:
        print(json.dumps({"market": a.market, "year": a.year, "rows": rows},
                         ensure_ascii=False, indent=2))
        return 0

    lo, hi = rows
    print(f"\nالسوق {a.market} · السنة {a.year}"
          f"{' · ' + a.product if a.product else ''}\n")
    print(f"{'الحقل':<26}{lo['hs_code']:>22}{hi['hs_code']:>22}")
    print("-" * 70)

    def line(label, a_, b_, unit=""):
        print(f"{label:<26}{_fmt(a_, unit):>22}{_fmt(b_, unit):>22}")

    line("الحكم", lo["verdict"], hi["verdict"])
    line("حجم السوق (استيراد $)", lo["market_size_usd"], hi["market_size_usd"])
    line("تركّز الموردين HHI", lo["hhi"], hi["hhi"])
    line("حصة السعودية %", lo["saudi_share_pct"], hi["saudi_share_pct"])
    print()
    for r in rows:
        if r["status"] != "ok":
            reason = {"fetch_failed": "تعذّر الجلب (شبكة/حدّ معدّل) — أعد "
                                      "المحاولة؛ ليس غيابَ بيانات",
                      "no_record": "لا سجلّ في كومتريد لهذا الرمز/السوق/السنة",
                      "unknown_market": "سوقٌ غير معروف في فهرس M49"}[r["status"]]
            print(f"  ⚠ {r['hs_code']}: {reason}")
    # فرقٌ يُحسَب فقط حين **كلا** الطرفين مرصود — لا فرقَ على فجوة.
    if lo["market_size_usd"] is not None and hi["market_size_usd"] is not None:
        d = hi["market_size_usd"] - lo["market_size_usd"]
        base = lo["market_size_usd"]
        pct = f" ({100.0 * d / base:+.1f}%)" if base else ""
        print(f"\n  الفرق في حجم السوق: {d:+,.0f}${pct}")
    else:
        print("\n  الفرق في حجم السوق: — (طرفٌ واحدٌ على الأقل غير مرصود؛ "
              "لا يُحسَب فرقٌ على فجوة)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
