"""قفل: مسار /research الرئيسي يكتب أكاديمياً افتراضياً — generation default.

طلب المالك (2026-07-23): «تأكّد أن الكاتب يكتب التقرير أكاديمياً». الاكتشاف:
البنية الأكاديمية كانت متاحةً فقط عبر تصدير/إعادة توليد بـ`?style=academic`؛
أمّا **التوليد الرئيسي** (`_run_research_pipeline` → `write_reviewed_report`)
فكان يستدعي الكاتب **بلا `style`**، فيستعمل العقد التجاري دوماً. النتيجة:
حتى التصدير الأكاديمي كان يعيد تشكيل نثرٍ كُتب بسجلٍّ تجاري.

الإصلاح (api.py): التوليد الرئيسي يمرّر النمط للكاتب، والافتراضي «academic»
عبر `SILK_REPORT_STYLE`. قيمة صريحة في الطلب (`report_style`) تتقدّم. هذه
الأقفال تثبت التوصيل نصّياً (لا نداء شبكة/كلود).
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_API = os.path.join(_ROOT, "api.py")


def _api_src() -> str:
    return open(_API, encoding="utf-8").read()


def test_research_request_has_report_style_field():
    """نموذج طلب البحث يحمل حقل نمط اختيارياً (تجاوز صريح للبيئة)."""
    src = _api_src()
    assert "report_style: str | None = None" in src


def test_default_report_style_helper_defaults_to_academic():
    """الافتراضي المضبوط «academic» (طلب المالك) عبر SILK_REPORT_STYLE."""
    src = _api_src()
    assert "def _default_report_style()" in src
    assert 'os.environ.get("SILK_REPORT_STYLE", "academic")' in src


def test_generation_passes_style_to_the_writer():
    """التوليد الرئيسي يمرّر النمط الفعّال للكاتب (لا استدعاء بلا style)."""
    src = _api_src()
    pipeline = src.split("def _run_research_pipeline(")[1].split(
        "\n    def ")[0]
    assert "eff_report_style = (report_style or _default_report_style())" in pipeline
    assert "style=eff_report_style," in pipeline
    # يُخزَّن النمط المستعمَل فعلاً كي تتّسق طبقة العرض/التصدير.
    assert '"report_style": eff_report_style,' in pipeline


# ملاحظة على صياغة القفلين أدناه (تعديل 2026-07-27): كانا يثبّتان **موضع**
# `report_style` كآخر وسيطٍ حرفياً (`"resume_reports, report_style)"`)، فكسرهما
# أيُّ معاملٍ جديدٍ يُضاف بعده ولو ظلّ التوصيل سليماً تماماً — وهذا ما حدث عند
# إضافة `hs_confidence`. الثابتُ المقصود ليس الموضع بل: **التوقيعان يقبلان
# النمط، والمسارانِ يمرّرانه**. أُعيدت الصياغة لتثبيت هذا الثابت وحده.
def test_both_call_sites_thread_the_request_style():
    """المساران المتزامن والخلفي يمرّران req.report_style (لا فقدان صامت)."""
    import ast
    src = _api_src()
    # المتزامن + وسائط الخيط الخلفي كلاهما يمرّر req.report_style.
    assert src.count("resume_reports, req.report_style") == 2
    # جسم الخيط الخلفي يمرّر المعاملَ الذي استلمه للأنبوب — يُفحَص نداءُ
    # الأنبوب نفسه (AST) لا نصٌّ حرفيّ، فلا يكسره معاملٌ جديدٌ بعده.
    bg = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_research_background")
    calls = [c for c in ast.walk(bg) if isinstance(c, ast.Call)
             and getattr(c.func, "id", "") == "_run_research_pipeline"]
    assert len(calls) == 1
    passed = ([getattr(a, "id", None) for a in calls[0].args]
              + [k.arg for k in calls[0].keywords])
    assert "report_style" in passed


def _optional_params(fn_name: str) -> dict:
    """معاملات دالةٍ داخلية في api.py وقيمها الافتراضية — قراءةُ AST بلا تنفيذ."""
    import ast
    for node in ast.walk(ast.parse(_api_src())):
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            args = node.args.args + node.args.kwonlyargs
            # المواقعُ الافتراضية تُحاذي ذيلَ args؛ المفتاحيةُ تحاذي kwonlyargs.
            pos_defaults = dict(zip([a.arg for a in node.args.args[
                len(node.args.args) - len(node.args.defaults):]],
                node.args.defaults))
            kw_defaults = dict(zip([a.arg for a in node.args.kwonlyargs],
                                   node.args.kw_defaults))
            names = [a.arg for a in args]
            return {"names": names, "defaults": {**pos_defaults, **kw_defaults}}
    raise AssertionError(f"{fn_name} not found in api.py")


def test_pipeline_and_background_accept_report_style_param():
    """توقيعا الدالتين يستقبلان النمط اختيارياً (تفادي TypeError عند التمرير).

    قفلٌ على **التوقيع** لا على نصّه: معاملٌ اختياريٌّ إضافي بعده لا يكسره،
    وحذفُ `report_style` أو جعلُه إلزامياً يكسره كما يجب."""
    import ast
    for fn_name in ("_run_research_pipeline", "_research_background"):
        sig = _optional_params(fn_name)
        assert "report_style" in sig["names"], fn_name
        default = sig["defaults"].get("report_style")
        assert isinstance(default, ast.Constant) and default.value is None, fn_name
