-- قمع المقارنة: عمودا القرار · the funnel's two decision columns (PR-7).
--
-- الحالتان `selected` و`drafted` في قيد `CHECK` منذ الترحيل 001، لكن لا عمود
-- يسجّل **ماذا** اختير ولا **أيّ** مسودّة ربُطت — فالحالة كانت ستكون اسماً بلا
-- مرجع. البديل الوحيد داخل المخطّط القائم كان تضييق `funnel_studies` حذفاً
-- (إبقاء الفائز وحده)، وهو يُهلِك سجلّ المقارنة نفسه: قمعٌ «قارَن عشر دراسات»
-- يصير بعد الاختيار كأنه قارَن واحدة، فلا يبقى ما يُراجَع.
--
-- إضافةٌ محضة (`ALTER TABLE ADD COLUMN`) على نمط الترحيلات في هذا الريبو: لا
-- صفّ قائم يتغيّر، والقيمة `NULL` تعني «لم يُتّخذ القرار بعد» صراحةً.
--
-- The 'selected'/'drafted' states existed in the CHECK constraint with no column
-- recording WHAT was selected or WHICH draft was attached. The only alternative
-- within the existing schema was to narrow funnel_studies by deletion, which
-- destroys the comparison record itself. Additive: NULL = decision not yet made.

ALTER TABLE comparison_funnels ADD COLUMN selected_study_id INTEGER REFERENCES studies(id);
ALTER TABLE comparison_funnels ADD COLUMN draft_id INTEGER REFERENCES drafts(id);
