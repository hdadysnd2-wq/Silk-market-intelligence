"""تدقيقُ رموز HS المخزَّنة — read-only sweep of stored analyses & caches.

**لماذا:** رمزٌ خاطئ لا يبقى في تشغيلةٍ واحدة — يُحفَظ في `analyses` ويُعاد
عرضُه في شريط «بحوثي السابقة» بمظهرٍ رسميّ، ويُعاد تشغيلُه عبر `resume`
(الذي **يتخطّى بوّابة تأكيد HS كلياً**)، وتُعاد كتابةُ تقريره عبر
`POST /analyses/{id}/report` من `found["hs_code"]` المخزَّن. فسجلٌّ واحدٌ
مسمومٌ يظلّ يُنتج تقاريرَ واثقةَ المظهر إلى الأبد.

**ماذا يفعل:** لكلّ تحليلٍ مخزَّن يقارن الرمزَ المحفوظ بما يعيده
`silk_hs_resolver.resolve()` **اليوم** لنفس اسم المنتج، ويُشغّل عقدَ التأكيد
الدلالي (`silk_hs_confirm.confirm_hs`) وبوّابةَ الثقة الجديدة على المحفوظ.
يُبلّغ عن كلّ خلاف. يمسح أيضاً ذاكرةَ تصنيف HS في مخزن الحقائق.

**قراءةٌ فقط:** صفر كتابة، صفر شبكة، صفر نداء كلود، صفر تكلفة. لا يُصلح
شيئاً — يُخرِج قائمةً يقرّر المالك بشأنها (LAW: المالك السلطة الوحيدة).

Usage:
    python3 tools/audit_stored_hs.py                 # المسارات الافتراضية
    SILK_DATA_DIR=/data python3 tools/audit_stored_hs.py     # قرص Railway
    python3 tools/audit_stored_hs.py --json          # مخرَجٌ آليّ

Exit code 1 حين يوجد خلافٌ واحدٌ على الأقل (صالحٌ لوظيفة CI/تشغيلٍ دوريّ).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _analyses_db() -> str:
    """مسارُ قاعدة التحليلات — نفس ترتيب `silk_storage._db_path` حرفياً."""
    explicit = os.environ.get("SILK_DB", "").strip()
    if explicit:
        return explicit
    base = os.environ.get("SILK_DATA_DIR", "").strip()
    if base:
        return os.path.join(base, "silk.db")
    return os.path.join("data", "silk.db")


def _rows(path: str) -> list[dict]:
    """صفوفُ التحليلات (المنتج + الرمز + الطابع) — [] حين لا قاعدة/لا جدول."""
    if not os.path.exists(path):
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        with conn:
            cur = conn.execute(
                "SELECT id, product, hs_code, created_at, kind, status "
                "FROM analyses ORDER BY id")
            return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error as e:
        print(f"  ⚠ تعذّرت قراءة {path}: {e}")
        return []


def audit_analyses(path: str) -> list[dict]:
    """قارِن كلَّ رمزٍ مخزَّن بما يحسمه المُحلِّل اليوم — قائمةُ الخلافات."""
    from silk_hs_confirm import confirm_hs, is_flagged, min_confidence
    from silk_hs_resolver import resolve

    findings: list[dict] = []
    for row in _rows(path):
        product = (row.get("product") or "").strip()
        stored = str(row.get("hs_code") or "").strip()
        if not product or not stored:
            continue
        dp = resolve(product)
        today_code = dp.value
        conf = confirm_hs(product, stored)
        reasons = []
        if today_code and today_code != stored:
            reasons.append(f"المُحلِّل اليوم يعيد {today_code} لا {stored}")
        if is_flagged(conf):
            reasons.append(f"التأكيد الدلالي يرفض المخزَّن "
                           f"(تداخل {conf.get('overlap')}, ينقص: "
                           f"{'، '.join(conf.get('missing_terms') or [])})")
        if today_code is None:
            reasons.append(f"المُحلِّل اليوم لا يحسم رمزاً لهذا الاسم "
                           f"({dp.note[:70]})")
        # الحالةُ الثالثة، وهي الأخبثُ: المُحلِّل يعيد **نفسَ** الرمز المخزَّن
        # فلا «خلاف» — لكنّ ثقتَه دون العتبة الجديدة، أي أنّ التشغيلة ما كانت
        # لتبدأ اليوم أصلاً («نفط خام» → 520100 قطن بثقة 0.71). صفٌّ كهذا يبقى
        # في السجلّ بمظهرٍ رسميّ ولا يلتقطه فحصُ الاختلاف وحده.
        below = (dp.value is not None and dp.confidence < min_confidence())
        if below:
            reasons.append(
                f"ثقةُ الحسم اليوم {dp.confidence} دون العتبة "
                f"{min_confidence()} — بوّابةُ الثقة الجديدة كانت ستحجبه")
        if not reasons:
            continue
        findings.append({
            "analysis_id": row.get("id"),
            "product": product,
            "stored_hs": stored,
            "resolver_today": today_code,
            "resolver_confidence": dp.confidence,
            "below_new_threshold": below,
            "stored_code_desc": conf.get("code_desc"),
            "stored_confirmed": conf.get("confirmed"),
            "created_at": row.get("created_at"),
            "kind": row.get("kind"),
            "status": row.get("status"),
            "reasons": reasons,
        })
    return findings


def audit_classify_cache() -> list[dict]:
    """امسح ذاكرةَ تصنيف HS في مخزن الحقائق — مرشّحون مخزَّنون يرفضهم التأكيد."""
    from silk_hs_confirm import confirm_against_description
    out: list[dict] = []
    try:
        import silk_store
        with silk_store.connect() as conn:
            try:
                cur = conn.execute(
                    "SELECT product_key, payload_json, created_at "
                    "FROM hs_classify_cache")
            except sqlite3.Error:
                return []          # الجدول غير مُرحَّل بعد — لا ذاكرة، لا خلاف
            rows = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — أداةُ تدقيق لا تُسقِط شيئاً
        print(f"  ⚠ تعذّر فتح مخزن الحقائق: {e}")
        return []
    for r in rows:
        key = r[0]
        try:
            payload = json.loads(r[1] or "{}")
        except ValueError:
            continue
        product = str(key or "").split("|")[0]
        for c in (payload.get("candidates") or []):
            code = str(c.get("hs6") or "")
            desc = c.get("description_ar") or c.get("description") or ""
            chk = confirm_against_description(product, code, desc)
            if chk.get("confirmed") is False:
                out.append({"product_key": key, "hs6": code,
                            "description": desc[:70],
                            "overlap": chk.get("overlap"),
                            "missing_terms": chk.get("missing_terms"),
                            "created_at": r[2]})
    return out


def main() -> int:
    as_json = "--json" in sys.argv
    db = _analyses_db()
    stored = audit_analyses(db)
    cached = audit_classify_cache()
    if as_json:
        print(json.dumps({"analyses_db": db, "stored_disagreements": stored,
                          "cached_disagreements": cached},
                         ensure_ascii=False, indent=2))
        return 1 if (stored or cached) else 0

    print(f"قاعدة التحليلات: {db}"
          f"{'' if os.path.exists(db) else '  (غير موجودة)'}")
    print(f"عدد الصفوف المفحوصة: {len(_rows(db))}")
    print()
    if not stored:
        print("✓ لا خلافَ بين رمزٍ مخزَّن وما يحسمه المُحلِّل اليوم.")
    else:
        print(f"⚠ {len(stored)} تحليلاً مخزَّناً يخالف رمزُه حُكمَ اليوم:\n")
        for f in stored:
            print(f"  #{f['analysis_id']}  {f['product']!r}  "
                  f"[{f['created_at']}, {f['kind']}/{f['status']}]")
            print(f"      المخزَّن: {f['stored_hs']} "
                  f"«{f['stored_code_desc']}»")
            print(f"      اليوم:   {f['resolver_today']} "
                  f"(ثقة {f['resolver_confidence']})")
            for r in f["reasons"]:
                print(f"      — {r}")
            print()
    print()
    if cached:
        print(f"⚠ {len(cached)} مرشّحاً في ذاكرة التصنيف يرفضه التأكيد الدلالي:")
        for c in cached:
            print(f"  {c['product_key']!r} → {c['hs6']} "
                  f"(تداخل {c['overlap']}, ينقص: "
                  f"{'، '.join(c['missing_terms'] or [])})")
    else:
        print("✓ لا مرشّحَ مرفوضاً في ذاكرة تصنيف HS.")
    return 1 if (stored or cached) else 0


if __name__ == "__main__":
    raise SystemExit(main())
