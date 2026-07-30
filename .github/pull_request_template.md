## ما هذا · what this is

<!-- ماذا يفعل هذا الفرق ولماذا. اربط الادعاءات بـfile:line حيث يفيد. -->

## المراجعة الذاتية · self-review (CLAUDE.md §58)

<!-- إلزامي: هذا القسم بوّابة CI (tools/check_self_review_gate.py).
     شغّل /code-review على الفرق العامل، ثم صرّح صراحةً بنتيجة الملاحظات
     عالية الخطورة بإحدى الصيغ:
       • «لا عيوب high» / "no high-severity findings"
       • «N ملاحظة مُصلَحة» / "N findings fixed"
       • «خطر مقبول» / "accepted risk" + مرجعه في docs/DEEP_RESEARCH_DECISIONS.md
     البوّابة تتحقّق من **وجود** التصريح لا من جودة المراجعة — لا تحذفها لتمرّ. -->

- [ ] شُغِّل `/code-review` على الفرق العامل
- [ ] كل ملاحظة high فأعلى: مُصلَحة أو مسجَّلة «خطر مقبول»

## حالة الدليل · evidence bucket

<!-- الدلاء الثلاثة (LAW §٢) — لا خلط:
     • «hermetic only» — pytest tests/ -q أخضر فقط (ليس جاهزاً للمالك)
     • «passed real-server + browser e2e» — رُتبتا ٢–٣ خضراوان أيضاً
     • «no sufficient evidence — pending» -->

## الحدود المعلنة · declared limits

<!-- ما ليس في هذا الفرق، وأي خطر متبقٍّ موثَّق ومكانه. -->
