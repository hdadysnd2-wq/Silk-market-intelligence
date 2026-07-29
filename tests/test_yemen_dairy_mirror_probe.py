"""قفلٌ هرمتيّ لمِجَسّ مرآة اليمن — no-fabrication contract on the probe.

المبدأ المؤسِّس: تعذّرُ الجلب (None) ليس «لا بيانات» (0.0). المِجَسّ يجب أن
يميّزهما صراحةً — صفرٌ مختلَقٌ عند فشل الشبكة يُضلِّل قرارَ المالك.

Run: python3 -m pytest tests/test_yemen_dairy_mirror_probe.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "tools"))


def test_fetch_failure_is_not_reported_as_zero():
    """`comtrade_trade` يعيد None (فشل جلب) => المِجَسّ None لا 0.0."""
    import probe_yemen_dairy_mirror as P
    P.comtrade_trade = lambda *a, **k: None            # فشل الجلب
    assert P._saudi_export_to_yemen("040120", 2023) is None


def test_genuine_empty_record_is_zero_not_none():
    """سجلّ حقيقيّ فارغ ([]) => 0.0 صريح (نجح الاستعلام، لا تصريح)."""
    import probe_yemen_dairy_mirror as P
    P.comtrade_trade = lambda *a, **k: []               # نجاح، سجلّ فارغ
    assert P._saudi_export_to_yemen("040110", 2023) == 0.0


def test_cell_renders_fetch_failed_distinctly_from_zero():
    import probe_yemen_dairy_mirror as P
    assert "FETCH_FAILED" in P._cell(None)
    assert "FETCH_FAILED" not in P._cell(0.0)
    assert "0" in P._cell(0.0)
