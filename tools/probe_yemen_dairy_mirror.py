#!/usr/bin/env python3
"""مِجَسّ مرآة اليمن للألبان — Yemen dairy mirror probe (تشخيصٌ صفر تكلفة).

> **الغرض (سؤال المالك قبل الموافقة على تشغيلةٍ جديدة).** قبل إنفاق ~$3 على
> إعادة تشغيل تحليل ٧ برمزٍ مُصحَّح، تحقّق أنّ الرمز المُصحَّح يحمل **بياناتٍ
> أفضل** لا مجرّد تسميةٍ أصحّ. يقارن هذا المِجَسّ تدفّقَ المرآة (تصريحُ تصدير
> الشركاء إلى اليمن) تحت 040110 مقابل 040120 مقابل 0402 عبر آخر خمس سنوات،
> لمُصدِّرٍ واحد (السعودية) وللعالم.
>
> **صفر تكلفة دولارية:** كومتريد مصدرٌ **مجاني** (ليس من المزوّدين المدفوعين
> Claude/LocalPrice/Volza/Explee). هذا المِجَسّ لا يمسّ المسار المدفوع إطلاقاً.
>
> **عقد عدم الاختلاق (المبدأ المؤسِّس).** يُميَّز صراحةً بين:
>   - `FETCH_FAILED` — تعذّر الوصول (شبكة/‏403 سياسة/‏429 حصّة): **ليس** «لا
>     بيانات»، لا يُقرَأ صفراً. (بيئةُ التطوير المعزولة تحجب `comtradeapi.un.org`
>     فيظهر هذا هنا — شغّله على النشر الحيّ حيث كومتريد مسموح.)
>   - `0` (سجلّ حقيقيّ فارغ) — الاستعلامُ نجح لكن لا تصريحَ تحت هذا الرمز/السنة.
>   - رقمٌ موجب — قيمةٌ حقيقية من كومتريد.

التشغيل (حيث كومتريد مسموح — النشر الحيّ):  python3 tools/probe_yemen_dairy_mirror.py
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from silk_data_layer import (comtrade_trade, comtrade_trade_mirror_total,  # noqa: E402
                             primary_value)

YEMEN_M49 = 887
SAUDI_M49 = 682
YEARS = [2020, 2021, 2022, 2023, 2024]
CODES = [
    ("040110", "طازج، دسم ≤1% (منزوع)"),
    ("040120", "طازج، دسم >1%–6% (كامل الدسم/UHT سائل)"),
    ("040210", "مركّز/مسحوق، دسم ≤1.5%"),
    ("040221", "مركّز/مسحوق، دسم >1.5%"),
    ("040229", "مركّز/مسحوق + سكّر"),
]


def _saudi_export_to_yemen(code: str, year: int):
    """صادراتُ السعودية إلى اليمن (reporter=682, partner=887, flow=X).
    None => تعذّر الجلب (FETCH_FAILED)؛ 0.0 => سجلّ حقيقيّ فارغ."""
    recs = comtrade_trade(code, SAUDI_M49, year, flow="X", partner=YEMEN_M49)
    if recs is None:
        return None
    vals = [v for v in (primary_value(r) for r in recs) if v is not None]
    return float(sum(vals)) if vals else 0.0


def _world_export_to_yemen(code: str, year: int):
    """إجمالي مرآة العالم لواردات اليمن (reporter=all, partner=887, flow=X)."""
    return comtrade_trade_mirror_total(code, YEMEN_M49, year, flow="M")


def _cell(v) -> str:
    if v is None:
        return f"{'FETCH_FAILED':>14}"
    return f"{v:>14,.0f}"


def _table(title: str, fn) -> dict:
    print(f"\n=== {title} (USD) ===")
    print(f"{'الرمز':10} {'الوصف':38} " + " ".join(f"{y:>14}" for y in YEARS))
    totals: dict = {}
    for code, desc in CODES:
        row, s = [], 0.0
        for y in YEARS:
            try:
                v = fn(code, y)
            except Exception as e:  # noqa: BLE001 — سجّل الفشل لا تُسقِطه
                v = None
                print(f"    [{code}/{y}] EXC {type(e).__name__}: {e}",
                      file=sys.stderr)
            row.append(v)
            if isinstance(v, (int, float)):
                s += v
        totals[code] = s
        print(f"{code:10} {desc:38} " + " ".join(_cell(v) for v in row))
    return totals


def main() -> int:
    print("مِجَسّ مرآة اليمن للألبان — 040110 مقابل 040120 مقابل 0402 (5 سنوات)")
    print("صفر تكلفة دولارية (كومتريد مجاني). FETCH_FAILED = تعذّر الوصول لا «لا بيانات».")
    saudi = _table("صادرات السعودية إلى اليمن (مرآة)", _saudi_export_to_yemen)
    world = _table("صادرات العالم إلى اليمن (مرآة إجمالية)", _world_export_to_yemen)

    print("\n=== الحكم (٥ سنوات مجموعة) ===")
    for code, desc in CODES:
        print(f"  {code} ({desc}): سعودية={saudi.get(code, 0):,.0f}$  "
              f"عالم={world.get(code, 0):,.0f}$")
    thin = saudi.get("040120", 0) == 0 and world.get("040120", 0) == 0
    if thin:
        print("\n⚠ مرآةُ 040120 فارغة/رقيقة عبر الفترة: تشغيلةٌ جديدة تحته تشتري "
              "تسميةً أصحّ **بلا بياناتٍ أفضل** — أعِد النظر قبل الإنفاق. "
              "(تحقّق أنّ FETCH_FAILED لا يُعمي الحكم — شغّله حيث كومتريد مسموح.)")
    else:
        print("\n✓ مرآةُ 040120 تحمل قيماً — الرمزُ المُصحَّح يجلب بياناتٍ فعلية.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
