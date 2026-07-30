"""العمود الميكانيكي لِـ docs/LESSONS.md — كل صفّ في السجلّ يسمّي أداة إنفاذ،
وهذا الملف يثبت أن كل أداة مسمّاة **لا تزال موجودة** في الشجرة. حذف حارس أو
وثيقة يكسر هذا الاختبار فيُحمرّ CI — فلا يمكن لأداة إنفاذ أن تختفي بصمت.

هذا اختبار **وجود/مرساة** لا إعادة تنفيذ للسلوك (الاختبارات السلوكية نفسها
تُفشِل على الانحدار). قيمته العظمى للبنود الموثَّقة فقط (١ و١٠) التي لا حارس
آخر لها: بدونه يبقى CI أخضر لو حُذِفت وثيقة التدقيق أو أُفرِغت.

قراءة الوجود فقط (Path.exists + تفتيش نصّي للمصدر) — هرمتي، بلا شبكة،
دون ثانية. لا يكرّر تأكيداً سلوكياً (ذلك يُضاعِف الصيانة).

Run: python3 -m pytest tests/test_lessons_enforcement.py -q
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel: str) -> str:
    with open(os.path.join(_ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _exists(rel: str) -> bool:
    return os.path.exists(os.path.join(_ROOT, rel))


# كل مدخلة: (رقم الدرس، مسار المصدر، سلاسل يجب أن تكون كلها حاضرة).
# المصادر المسمّاة هنا هي بالضبط عمود «الإنفاذ» في docs/LESSONS.md.
_SYMBOL_ANCHORS = [
    # البند ٢ — المُصدِّرات تقرأ فرع deep_research لا قالب /analyze
    (2, "silk_render.py", ["_deep_research_view"]),
    (2, "silk_reports.py", ["_md_deep_research", "render_client_docx"]),
    # البند ٤ — مصيدة إقلاع التخزين الفاني + تحذير /health
    (4, "api.py", ["SILK_REQUIRE_PERSISTENT_DATA_DIR",
                   "SILK_DATA_DIR غير مضبوط"]),
    # البند ٥ — نقاط تفتيش/استئناف البعثات
    (5, "silk_storage.py", ["def create_research_run", "def save_mission_checkpoint",
                            "def load_mission_checkpoints", "def mark_research_failed"]),
    # البند ٦ — مستخلِصات JSON المتينة (الاسم الصحيح: لا _extract_json في
    # silk_llm_runtime — بل _json_candidates/_parse_output؛ _extract_json في
    # silk_ai_judge وحدها)
    (6, "silk_llm_runtime.py", ["_json_candidates", "_parse_output",
                                "_JSON_PARSE_FAILURE_GAP"]),
    (6, "silk_ai_judge.py", ["_extract_json"]),
    # البند ٧ — معامل source للبنك الدولي + تدهور البعثة لفجوة معلنة
    (7, "silk_data_layer.py", ["_WB_INDICATOR_SOURCE", "_wb_shape_error"]),
    (7, "silk_agents.py", ["class BaseAgent"]),
    # البند ٨ — عقد DataPoint (لا اختلاق)
    (8, "silk_data_layer.py", ["class DataPoint"]),
    # البند ٤٢ — تحليل #1 DZA: تنقية Markdown شارد/ثقة خام + إصلاح عمود
    # العملة + علم مراجع حتمي لتكرار رقم مفتاحي.
    (42, "silk_render.py", ["_strip_stray_markdown", "_AR_RAW_CONF_RE",
                           "_fix_price_column_currency_label"]),
    (42, "silk_ai_judge.py", ["_repeated_key_figure_issues"]),
    (42, "silk_quality_gate.py", ["currency_label_mismatch"]),
    (42, "tools/canonical_dza_peanut_butter.py", ["def dza_research_blob"]),
    # البند ٤٣ — المُصنِّف العام: صمّام فشل-آمن مفعَّل افتراضياً.
    (43, "silk_hs_classifier.py", ["def enabled", '"0", "false", "no", "off"']),
    (43, "api.py", ['health["hs_classifier"]']),
    # البند ٤٤ — Master Prompt Part 2 §B: _verdict_tone تتعرّف على التسمية
    # العربية أيضاً، وبوابة اتساق الحكم عند التسليم.
    (44, "silk_render.py", ["عدم الدخول", "مشروط", "مراقبة"]),
    (44, "silk_reports.py", ["_assert_verdict_consistency_doc",
                            "_assert_verdict_consistency_text",
                            "_declared_verdict_labels", "_resolve_vtxt"]),
    # البند ٤٥ — دالة الإصلاح الشقيقة لعمود العملة تحمل نفس تضييق نافذة
    # الجدول؛ عيّنة العميل مطابقة لعقد الكاتب المُهجَّر (بعملة الرصد).
    (45, "silk_render.py", ["_fix_price_column_currency_label"]),
    (45, "tools/gen_client_report_sample.py", ["السعر/كجم (بعملة الرصد)"]),
]

# كل مدخلة: (رقم الدرس، مسار الوثيقة، علامات المنهج التي يجب أن تبقى).
_DOC_ANCHORS = [
    # البند ١ — merged ≠ works؛ الدليل الحيّ بأثر
    (1, "docs/LIVE_PROOF_RUNBOOK.md", ["لا يُشغَّل هيرمتياً"]),
    (1, ".claude/skills/pr-and-wave-discipline/SKILL.md",
     ["direct reproduction", "static code review",
      "no sufficient evidence — pending"]),
    # البند ١٠ — التدقيق قراءة فقط، بالدليل، وBLOCKED جواب صادق
    (10, "docs/AUDIT_STATUS.md", ["قراءة فقط", "غير موجود"]),
    (10, ".claude/skills/pr-and-wave-discipline/SKILL.md",
     ["no sufficient evidence — pending"]),
    # البند ١٥ — دلاء الصدق المنقسمة (hermetic only مقابل real-server+browser)
    (15, ".claude/skills/pr-and-wave-discipline/SKILL.md",
     ["hermetic only", "passed real-server + browser e2e", "e2e-live-shape"]),
    # البند ٥٨ — المراجعة الذاتية (/code-review) قبل فتح/وسم أي PR جاهزًا؛ قاعدة
    # عملية (عائلة البندين ١/١٠) بلا اختبار سلوكي — العلامات في CLAUDE.md +
    # مهارة pr-and-wave-discipline §7 (سابقة Yemen stale-tag). حذف القاعدة
    # من أيّهما يُحمِّر هذا الاختبار.
    (58, "CLAUDE.md",
     ["/code-review",
      "self-review catches what hermetic tests structurally cannot"]),
    (58, ".claude/skills/pr-and-wave-discipline/SKILL.md",
     ["/code-review", "Yemen stale-tag",
      "self-review catches what hermetic tests"]),
]

# كل مدخلة: (رقم الدرس، ملف الاختبار، دوال اختبار يجب أن تبقى).
_TEST_ANCHORS = [
    (2, "tests/test_research_export_from_view.py",
     ["def test_report_md_renders_deep_research_not_analyze_template"]),
    (3, "tests/test_research_export_from_view.py",
     ["def test_report_docx_client_does_not_501_on_judgment_language"]),
    (3, "tests/conftest.py", ["def docx_all_text"]),
    (4, "tests/test_analysis_history_storage.py",
     ["def test_health_warns_when_silk_data_dir_unset"]),
    (4, "tests/test_persistent_volume.py",
     ["def test_create_app_refuses_ephemeral_storage_when_require_flag_set"]),
    (5, "tests/test_wave13_resilience.py",
     ["def test_mid_run_crash_then_resume_skips_completed_missions"]),
    (5, "tests/test_persistent_volume.py",
     ["def test_redeploy_preserves_research_checkpoints_and_resume_reads_them"]),
    (6, "tests/test_technical_mission_failures_item2.py",
     ["def test_json_repair_retry_stays_declared_gap_when_repair_also_fails"]),
    (7, "tests/test_wave_p4_source_outages.py",
     ["def test_every_mission_governance_indicator_is_source3_registered"]),
    (8, "tests/test_smoke.py",
     ["def test_tradeflow_all_records_missing_value_is_declared_gap"]),
    (9, "tests/test_item3_analyze_screen_button.py",
     ["def test_all_action_buttons_have_honest_tooltips_and_distinct_labels"]),
    (9, "tests/test_ui_action_buttons_have_purpose.py",
     ["def test_every_runbar_action_button_has_a_tooltip"]),
    (11, "tests/test_client_sanitizer_covers_guard.py",
     ["def test_every_arabic_guard_trigger_is_neutralized_by_the_sanitizer",
      "def test_dlreport_surfaces_the_501_detail_not_bare_status"]),
    (12, "tests/test_limits_reconciliation_b1.py",
     ["def test_resolved_supplier_share_gap_is_retagged_not_contradiction",
      "def test_genuinely_unresolved_gap_stays_verbatim"]),
    (13, "tests/test_client_export_redact_not_refuse.py",
     ["def test_render_client_docx_does_not_501_on_english_source_title"]),
    (14, "tests/test_report_output_overhaul.py",
     ["def test_quality_gate_fails_on_confidentiality_leak_tokens",
      "def test_docx_is_rtl_document_wide",
      "def test_finding_assembly_uses_public_source_not_tool_use"]),
    (15, "tests/test_rung2_real_server.py",
     ["def test_report_md_serves_real_narrative_not_the_empty_analyze_template",
      "def test_report_docx_downloads_a_real_openable_document_no_501"]),
    (15, "tests/test_rung3_playwright_e2e.py",
     ["def test_rung3_full_browser_flow_word_and_md_export_and_sidebar"]),
    (16, "tests/test_command6_regression_budget_and_pricing.py",
     ["def test_full_report_with_all_blocks_completes_end_to_end_not_skeleton",
      "def test_writer_continuation_call_uses_the_ceiling_not_the_base_budget",
      "def test_every_default_routed_model_is_priced",
      "def test_maxtokens_truncated_call_still_meters_its_burned_tokens"]),
    # البند ١٧ — ريبر DataPoint المختصر/الشاذ مرّ نصف مترجم (هجوم المشرف الحي)؛
    # الحارس: النمط المرن + شبكة الأمان، وسلاسل المشرف الحرفية في السجل.
    (17, "tests/test_regression_registry.py",
     ["def _guard_datapoint_repr_flexible"]),
    # البند ١٨ — تسريب اسم مزوّد داخلي للعميل (بلاغ UK الحي): كنس المدوّنة
    # القانونية + شكل UK بزيرو تطابق، والحارس السلوكي في السجل.
    (18, "tests/test_vendor_name_leak_item1.py",
     ["def test_client_export_names_no_vendor_across_canonical_and_uk_shapes",
      "def test_client_vendor_guard_fails_loud_on_injected_vendor_name"]),
    (18, "tests/test_regression_registry.py",
     ["def _guard_vendor_name_leak"]),
    # البند ١٩ — عقد صيغة التصدير (زرّ PDF كان ينزّل docx): الحارس السلوكي في
    # السجل + تدفّق المتصفّح الحقيقي يؤكّد توقيع %PDF.
    (19, "tests/test_regression_registry.py",
     ["def _guard_export_format_contract"]),
    # البند ٢٠ — تغطية العالم (الميزة أ): لا تلفيق فئة-٢ ولا تفجّر ميزانية؛
    # الأقفال السلوكية + الحارس السلوكي في السجل.
    (20, "tests/test_world_coverage_tierA.py",
     ["def test_tier_separation_and_labels",
      "def test_tier2_never_carries_a_local_csv_value",
      "def test_tier2_gather_makes_zero_comtrade_calls",
      "def test_budget_exhausted_degrades_to_tier1_only"]),
    (20, "tests/test_regression_registry.py",
     ["def _guard_world_tier2_no_fabrication"]),
    # البند ٢١ — استقبال المنتج من صورة (الميزة ب): لا اختلاق منتج، والمحوّل
    # أماميّ معزول؛ الأقفال السلوكية + الحارس السلوكي في السجل.
    (21, "tests/test_product_intake_featureB.py",
     ["def test_low_confidence_or_unreadable_never_fabricates",
      "def test_intake_module_imports_no_pipeline_code",
      "def test_endpoint_image_call_is_metered_from_the_cap"]),
    (21, "tests/test_regression_registry.py",
     ["def _guard_intake_no_silent_guess"]),
    # البند ٢٢ — بوّابة «خارج التغطية» (الميزة أ): سوق خارج التغطية لا دراسة
    # هزيلة بل رسالة صادقة + إشارة طلب؛ الأقفال + الحارس السلوكي في السجل.
    (22, "tests/test_out_of_coverage_guard.py",
     ["def test_out_of_coverage_market_returns_honest_message_and_logs_demand",
      "def test_flag_off_no_coverage_guard_any_country_works_todays_way"]),
    (22, "tests/test_regression_registry.py",
     ["def _guard_out_of_coverage_thin_study"]),
    # البند ٢٣ — الفيتوتشيني: لا حجز/إنفاق برمز HS غير محسوم؛ البوّابة الصلبة
    # + المُصنِّف المقيس + الحارس السلوكي في السجل.
    (23, "tests/test_wave1_hs_classifier.py",
     ["def test_research_hard_gate_422_on_empty_hs6_no_reservation",
      "def test_endpoint_low_confidence_is_metered_count_from_the_cap"]),
    (23, "tests/test_regression_registry.py",
     ["def _guard_unresolved_hs_silent_spend"]),
    # البند ٢٤ — الحارسان قاعدتان مبنيّتان على البيانات لا حالتا منتج؛ قفل
    # التعميم (≥٤ عيّنات) + غياب الترميز الصلب + الحارس السلوكي في السجل.
    (24, "tests/test_wave1_hs_classifier.py",
     ["def test_classifier_and_advisory_paths_have_no_hardcoded_product_or_iso_or_hs",
      "def test_producer_advisory_generalizes_from_data_not_names"]),
    (24, "tests/test_regression_registry.py",
     ["def _guard_hardcoded_product_rule"]),
    # البند ٢٥ — عائلة A (الدراسة بالاتجاه الخاطئ): أشقّاء config-driven +
    # القفل بلا ISO/HS صلب + الحارس السلوكي في السجل.
    (25, "tests/test_wave1p5_prerun_advisories.py",
     ["def test_self_origin_advisory_fires_for_origin_market_config_driven",
      "def test_prerun_logic_has_no_hardcoded_market_or_hs_literal"]),
    (25, "tests/test_regression_registry.py",
     ["def _guard_wrong_direction_study"]),
    # البند ٢٦ — عائلة C (الفشل الصامت لخدمةٍ خارجية): إعلانُ الفشل للمشغّل +
    # جدول التدقيق + الحارس السلوكي في السجل.
    (26, "tests/test_wave1p5_service_failure_ops.py",
     ["def test_scraper_submit_failure_emits_service_ops_entry",
      "def test_keyless_agent_failure_emits_service_ops_entry"]),
    (26, "tests/test_regression_registry.py",
     ["def _guard_silent_external_failure"]),
    (26, "docs/EXTERNAL_SERVICES_FAILURE_AUDIT.md",
     ["service → failure path"]),
    # البند ٢٧ — عائلة D (الإنفاق قبل المعرفة): لوحة الجاهزية قبل الحجز +
    # الرُتبة ٣ للوحة + الحارس السلوكي في السجل.
    (27, "tests/test_wave1p5_prerun_advisories.py",
     ["def test_readiness_panel_lists_blocking_and_advisory_before_run",
      "def test_readiness_is_read_only_no_reservation"]),
    (27, "tests/test_rung3_playwright_e2e.py",
     ["def test_rung3_readiness_panel_flow_checklist_before_confirm"]),
    (27, "tests/test_regression_registry.py",
     ["def _guard_readiness_before_spend"]),
    # البند ٢٨ — نقاء جدول الروابط (جغرافيا/نثر/حشو) على المدوّنة القانونية.
    (28, "tests/test_wave2_first_pdf_cluster.py",
     ["def test_wrong_geo_lead_dropped_valid_kept",
      "def test_prose_leak_sentence_never_becomes_a_lead_row",
      "def test_filler_all_dash_lead_dropped"]),
    (28, "tools/canonical_fettuccine.py", ["def fettuccine_research_blob"]),
    (28, "tests/test_regression_registry.py",
     ["def _guard_leads_table_hygiene"]),
    # البند ٢٩ — «سلك» متّصلة + A4 + القفل البصري.
    (29, "tests/test_wave2_first_pdf_cluster.py",
     ["def test_docx_brand_is_shape_safe_no_combining_marks",
      "def test_docx_page_size_is_a4_not_letter"]),
    (29, "tests/test_regression_registry.py",
     ["def _guard_report_arabic_shape_a4"]),
    # البند ٣٠ — لا اسم منتجٍ مثبَّت في القوالب (توسيع hardcoded-product-rule).
    (30, "tests/test_wave2_first_pdf_cluster.py",
     ["def test_disclaimer_parametrized_by_study_product_not_dates",
      "def test_no_hardcoded_product_word_in_client_facing_templates"]),
    (30, "tests/test_regression_registry.py",
     ["def _guard_client_template_no_hardcoded_product"]),
    # البند ٣١ — تخزين /analyze للقاعدة القانونية لا قرصٍ نسبيّ فانٍ (المعرّف
    # «1» ثم 404): التدفّق الحيّ الكامل + الحارس السلوكي + خطوة الدخان.
    (31, "tests/test_analyze_persistence_and_glyph.py",
     ["def test_engine_persist_writes_to_canonical_db_path_not_relative_literal",
      "def test_quick_scan_analyze_full_persisted_flow_no_404",
      "def test_compare_all_markets_analyze_shares_the_same_fixed_flow",
      "def test_no_section_glyph_in_client_facing_strings"]),
    (31, "tests/test_regression_registry.py",
     ["def _guard_analyze_persist_canonical_db"]),
    (31, "tools/post_deploy_smoke.py", ["def _check_exports"]),
    # البند ٣٢ — مصدرٌ جديد = نفس العقود (فجوة معلنة/ops/مخزَّن/محكوم/نظيف الشروط).
    (34, "tests/test_wave_datasources_integration.py",
     ["def test_imf_declared_gap_on_fetch_failure_and_ops_logged",
      "def test_wto_no_key_is_declared_gap_with_zero_network_calls",
      "def test_tariff_fallback_prefers_wto_when_available",
      "def test_preferred_domains_map_keys_all_have_web_search_tool",
      "def test_new_source_modules_do_no_html_scraping",
      "def test_world_bank_arabic_portal_only_for_client_citation"]),
    (34, "docs/DECISIONS.md",
     ["INTEGRATED-with-artifact", "SEARCH-BIASED",
      "REJECTED as a data source"]),
    # البند ٣٥ — بوّابة HS فشل-آمن + نقطة اختناق مشتركة (تقرير الكويت الحيّ).
    (35, "silk_hs_confirm.py", ["def preflight_block"]),
    (35, "tests/test_report_quality_upgrade.py",
     ["def test_w1_2_research_gate_on_by_default_blocks_unconfirmed_hs",
      "def test_w2_hs_gate_blocks_on_both_analyze_and_research_by_default",
      "def test_w2_hs_gate_choke_point_is_shared_not_duplicated"]),
    (35, "tests/test_regression_registry.py",
     ["def _guard_hs_gate_shared_choke_point_fail_safe"]),
    # البند ٣٦ — تسرّب اليمن↔الكويت عبر نقاط تفتيش بعثات /research.
    (36, "silk_storage.py", ["market_iso3"]),
    (36, "tests/test_cross_market_leak_guard.py",
     ["def test_resume_with_different_market_is_rejected_409_not_silently_served",
      "def test_checkpoint_store_rejects_foreign_market_even_if_api_gate_bypassed"]),
    (36, "tests/test_regression_registry.py",
     ["def _guard_cross_market_checkpoint_leak"]),
    # البند ٣٧ — الاختبار الذهبي: كل العقود معاً على نفس سيناريو الحادثة.
    (37, "tools/canonical_kuwait_peanut_butter.py", ["def kuwait_research_blob"]),
    (37, "tests/test_golden_deep_research_contract.py",
     ["def test_golden_a_zero_cross_market_leak_in_kuwait_view",
      "def test_golden_b_hs_gate_blocks_kuwait_peanut_butter_on_both_paths_live",
      "def test_golden_b_resume_of_kuwait_run_as_different_market_is_rejected_live"]),
    (37, "tools/post_deploy_smoke.py", ["بوّابة تأكيد HS الحيّة"]),
    # البند ٣٨ — الحارس: مراقبةٌ دائمة للمالك حصراً، صفر تلوّث للعميل.
    (38, "silk_watchdog.py", ["def observe", "def render_report_md",
                              "def trend_report"]),
    (38, "tests/test_watchdog.py",
     ["def test_cross_market_leak_seeded_violation_is_red",
      "def test_clean_run_is_overall_green",
      "def test_watchdog_crash_is_isolated_never_raises",
      "def test_no_watchdog_strings_reach_rendered_client_markdown",
      "def test_three_known_service_failures_produce_yellow_findings"]),
    (38, "tests/test_regression_registry.py",
     ["def _guard_watchdog_owner_only_no_client_contamination"]),
    # البند ٣٩ — المصنّف العام: جدول البحث تلميحٌ ابتدائي لا حاكمٌ نهائي.
    (39, "silk_hs_classifier.py",
     ["def classify_general", "def _validated_candidate",
      "def _claude_classify_general"]),
    (39, "silk_hs_resolver.py", ["VALID_HS_CHAPTERS", "def chapter_valid"]),
    (39, "silk_hs_confirm.py", ["def confirm_against_description"]),
    (39, "silk_store.py",
     ["def cache_hs_classification", "def get_cached_hs_classification"]),
    (39, "tests/test_hs_general_classifier.py",
     ["def test_battery_never_auto_passes_wrong_chapter_without_llm",
      "def test_classify_general_never_auto_passes_flagged_product_without_llm",
      "def test_repeat_product_hits_cache_zero_extra_llm_calls"]),
    (39, "tests/test_regression_registry.py",
     ["def _guard_general_hs_classifier_no_lookup_table_ceiling"]),
    # البند ٤٠ — UI-ONLY FIX: نقطة اختناق tier واحدة، لا مسار واجهةٍ ثانٍ
    # يثق بـhs6 خامًا.
    (40, "web/index.html", ["function ensureHs(", 'res.tier==="auto"']),
    (40, "tests/test_wave1_hs_classifier.py",
     ["def test_web_ui_never_shows_auto_badge_from_unverified_source"]),
    (40, "tests/test_rung3_playwright_e2e.py",
     ["def test_rung3_ui_tier_consumption_locked_across_product_families"]),
    (40, "tests/test_regression_registry.py",
     ["def _guard_ui_tier_consumption_single_choke_point"]),
    # البند ٤١ — ONE FIX: المصادَق فعلياً من كلود يتصدّر على المرفوض
    # الحتمي؛ نواة التداخل ترفض تصادف جذرٍ قصير.
    (41, "silk_hs_classifier.py", ["def _rank_key"]),
    (41, "silk_hs_confirm.py", ["_MIN_CONTAINMENT_LEN", "def _covered"]),
    (41, "tests/test_hs_general_classifier.py",
     ["def test_breadth_active_resolution_surfaces_correct_primary_not_rejected_or_blank"]),
    (41, "tests/test_regression_registry.py",
     ["def _guard_active_resolution_beats_rejected_and_short_root_collision"]),
    # البند ٤٢ — تحليل #1 DZA: ست نتائج فشل بوّابة الجودة معاً (Markdown
    # شارد، ثقة خام، تكرار رقم مفتاحي، عمود سعر مضلِّل، سقف الملحق).
    (42, "tests/test_dza_quality_gate_fixes.py",
     ["def test_overall_verdict_moves_from_fail_to_pass_with_warnings"]),
    (42, "tests/test_regression_registry.py",
     ["def _guard_dza_quality_gate_six_findings"]),
    (43, "tests/test_hs_general_classifier.py",
     ["def test_general_classifier_valve_is_fail_safe_on_by_default"]),
    (43, "tests/test_regression_registry.py",
     ["def _guard_hs_classifier_valve_fail_safe_default"]),
    # البند ٤٤ — Master Prompt Part 2 §B: بوابة اتساق الحكم عند التسليم.
    (44, "tests/test_master_prompt_part2_verdict.py",
     ["def test_verdict_tone_recognizes_arabic_labels_not_only_english_codes",
      "def test_kuwait_client_and_research_docx_pass_verdict_gate"]),
    (45, "tests/test_dza_quality_gate_fixes.py",
     ["def test_5b_price_fix_scoped_to_table_not_whole_document",
      "def test_5b_price_fix_still_fires_within_the_same_table_block"]),
    # البند ٥٦ — بوّابة التغطية كانت تفشل مفتوحةً (تدقيق v2، الموجة ١): سُلَّم
    # fallback + السنة المشتركة؛ الأقفال السلوكية + الحارس في السجل.
    (56, "tests/test_out_of_coverage_guard.py",
     ["def test_coverage_gate_closes_when_current_year_empty_but_study_year_full",
      "def test_world_import_totals_resolved_ladders_to_first_nonempty_year"]),
    (56, "tests/test_regression_registry.py",
     ["def _guard_coverage_gate_year_fallback"]),
    # البند ٥٧ — ست صيغ تشويش نفذت من المعقِّم (تسريبات المشرف): تطبيعٌ قبل
    # المطابقة؛ الحارس السلوكي بالسلاسل الست الحرفية في السجل.
    (57, "tests/test_regression_registry.py",
     ["def _guard_sanitizer_obfuscation_variants"]),
    # البند ٧٥ — بوّابةٌ مرّت التركيبيّ ثم صمتت على الحقيقيّ (A3/تحليل ٧): كلّ
    # بوّابةٍ بقفلين (تركيبيّ + انحدارٍ من عرضٍ حقيقيّ الشكل يؤكّد الإطلاق).
    (75, "tests/test_gate_regression_locks_analysis7.py",
     ["def test_a3_real_lock_fires_on_dollar_symbol_form",
      "def test_mirror_real_lock_fires_on_analysis7",
      "def test_stale_real_lock_fires_on_analysis7",
      "def test_full_gate_fails_analysis7_view"]),
    (75, "tests/test_regression_registry.py",
     ["def _guard_gate_passes_synthetic_but_silent_on_real"]),
]

# حراس رمزية للبندين ١٢/١٣ (المصالحة + نقِّ-لا-ترفض) — وجود الدوال في المصدر.
_SYMBOL_ANCHORS_EXTRA = [
    (12, "silk_render.py", ["_reconcile_mission_limits", "_first_clause"]),
    (13, "silk_reports.py", ["_client_redact_residual"]),
    (13, "tools/post_deploy_smoke.py", ["report.docx"]),
    # البند ١٤ — تحديث مخرجات تقرير البحث (سرّية/عملة/RTL/اكتمال/PDF/أسلوب)
    (14, "silk_quality_gate.py", ["_check_confidentiality_leaks",
                                  "_check_style", "_check_trailing_ellipsis"]),
    (14, "silk_reports.py", ["def _apply_rtl", "def docx_to_pdf",
                            "def _clean_source_label", "def _trim_sentence"]),
    (14, "silk_render.py", ["_map_mission_keys", "_CLAUDE_WORD_RE"]),
    # البند ١٥ — المالك آخِر تأكيد لا أوّل مكتشف؛ رُتب الاختبار ٢–٣ + الوظيفة
    # المطلوبة e2e-live-shape + المُنشئ القانوني للمدوّنة الحقيقية الشكل.
    (15, "tools/live_shape_server.py",
     ["class LiveShapeServer", "def seed_db", "netherlands_research_blob"]),
    (15, "tools/canonical_netherlands.py", ["def netherlands_research_blob"]),
    (15, ".github/workflows/e2e-live-shape.yml", ["e2e-live-shape"]),
    # البند ١٥ — ميزانية الكاتب/المحلل أُعيد قياسها؛ نداء الإكمال يأخذ السقف.
    (16, "silk_ai_judge.py", ["_WRITER_MAX_TOKENS", "_MAX_TOKENS_CEILING",
                              "max_tokens=_MAX_TOKENS_CEILING"]),
    (16, "silk_market_analyst.py", ["_ANALYST_MAX_TOKENS"]),
    # البند ١٧ — النمط المرن + شبكة أمان DataPoint في المعقِّم نفسه.
    (17, "silk_render.py", ["_DATAPOINT_REPR_RE", "_DATAPOINT_ANY_RE"]),
    # البند ١٨ — قائمة أسماء المزوّدين الممنوعة على سطح العميل (بلاغ UK الحي):
    # المُطهِّر + المنقِّي + الحارس، وسطر next_step بلا اسم مزوّد.
    (18, "silk_reports.py", ["_CLIENT_VENDOR_RE", "_CLIENT_VENDOR_GENERIC",
                             "vendor_name"]),
    # البند ١٩ — عقد صيغة التصدير: الزرّ الأساسي يُسلّم PDF، والخادم يخدمه،
    # ومحرّك التحويل مثبَّت على صورة النشر (لا CI فقط).
    (19, "web/index.html", ['dlReport("pdf")', 'kind==="pdf"',
                            'data-act="pdf"']),
    (19, "api.py", ["report.pdf", 'media_type="application/pdf"']),
    (19, "Dockerfile", ["libreoffice-writer"]),
    # البند ٣٢ — إصلاحُ المحرّك لا تحرير التقرير (تدقيق زبدة الفول السوداني/
    # اليمن): كل عائلة عيبٍ تحريريّ صارت قاعدةَ عقدٍ + إنفاذ عرضٍ حتميّ + قفلًا.
    (32, "silk_hs_confirm.py", ["def confirm_hs", "def is_flagged",
                                "CONTEXTUAL_TAG"]),
    (32, "silk_render.py", ["_tag_stale_years", "_flip_conditions",
                            "_price_row_reason", "_has_seasonality_gap"]),
    (32, "silk_trends_agent.py", ["def broaden_if_weak",
                                  "SEASONALITY_GAP_CLOSURE"]),
    (32, "silk_style_contract.py", ["ALARMIST_PHRASES",
                                    "PROFESSIONAL_TONE_RULE"]),
    (32, "silk_ai_judge.py", ["def _alarmist_issues"]),
    (32, "tools/canonical_yemen.py", ["def yemen_research_blob"]),
    (32, "tests/test_report_quality_upgrade.py",
     ["def test_w1_2_hs_confirm_flags_peanut_butter_but_not_valid_matches",
      "def test_w6_1_watch_verdict_has_structured_flip_conditions"]),
    # البند ٣٣ — حلِّل المصدر لا النثر (parse provenance, not prose): قاعدةُ
    # الإفصاح تُرسى إلى بياناتٍ بنيوية، والمطابقة النصّية شبكةُ أمانٍ أخيرة.
    (33, "silk_staleness.py", ["def fact_year", "def is_stale_fact",
                              "def stale_fact_years", "def stale_tag"]),
    (33, "silk_ai_judge.py", ["from silk_staleness import"]),
    (33, "silk_render.py", ["stale_fact_years", "def _tag_stale_years"]),
    # الحقل البنيويّ data_year هو مصدر الفِنتيج (لا وسم نصّيّ year=).
    (33, "silk_data_layer.py", ["data_year"]),
    (33, "tests/test_report_quality_upgrade.py",
     ["def test_w2_1_fact_year_reads_structured_provenance_not_prose",
      "def test_w2_1_stale_fact_tagged_regardless_of_phrasing",
      "def test_w2_1_hs_heading_2008_never_tagged_no_stale_fact_behind_it"]),
    # البند ٤٦ — حزمة الفكس v2.1 (زبدة الفول السوداني/الكويت): بوابة الجودة
    # شرط تسليم للعميل (409 على FAIL) + عائلة فحوصات كاتب/عرض بنيوية + «المراجع»
    # تحلّ محلّ سجل الأدلة في بناء العميل.
    (46, "api.py",
     ["def _block_client_export_if_gate_failed",
      "def _gate_verdict_for_client_export"]),
    (46, "silk_watchdog.py", ["def record_blocked_export"]),
    (46, "silk_quality_gate.py",
     ["def _check_orphan_short_token",
      "def _check_dangling_cross_reference",
      "def _check_near_duplicate_figures",
      "def _check_hhi_false_precision",
      "def _check_supplier_rank_contiguity",
      "def _check_stray_percent_punctuation",
      "def _check_entity_near_duplicates",
      "def _check_confidence_band_label",
      "def _check_lpi_edition_year",
      "def _check_recommendation_tier_label_consistency"]),
    (46, "silk_reports.py", ["def _client_references_section"]),
    (46, "silk_render.py",
     ["def _already_explained_nearby", "def _year_in_growth_span",
      "def _fix_stray_percent_punctuation"]),
    (46, "tests/test_fix_pack_v2_1.py",
     ["def test_orphan_short_token_flagged",
      "def test_near_duplicate_figures_flagged",
      "def test_hhi_false_precision_flagged",
      "def test_lpi_invalid_edition_year_flagged",
      "def test_confidence_band_mismatch_flagged"]),
    (46, "tests/test_client_report_export.py",
     ["def test_client_docx_export_blocked_409_when_gate_fails",
      "def test_client_pdf_export_blocked_409_when_gate_fails",
      "def test_gate_crash_treated_as_fail_for_client_export"]),
    # صفوف 47-53 — برنامج إصلاح جودة التقارير (WP-1…WP-7)؛ الحُرّاس السلوكية
    # الكاملة في tests/test_regression_registry.py (_guard_wp1…_guard_wp7)
    # وملفات tests/test_wp*.py — هذه مراسي الرموز.
    (47, "silk_narrative.py", ["def authoritative_verdict"]),
    # معاملات المعاينة لم تعد سطراً حرفياً في الحمولة — صارت قراراً واعياً
    # بالنموذج (temperature=0 حيث تُقبَل، ونزعُ الثلاثة حيث يردّ المزوّد 400).
    # المراسي: الدالة الحاكمة + المنقّي (نقطة الاختناق) + موضع الإسناد.
    (47, "silk_llm_provider.py", ["def _supports_sampling_params",
                                  "def _scrub_sampling_params",
                                  '["temperature"] = 0']),
    (47, "silk_style_contract.py", ["def confidence_band_label"]),
    (48, "silk_reports.py", ["def _client_prose",
                             "def _client_missing_narrative_heads"]),
    (48, "silk_ai_judge.py", ["def rephrase_client_sections"]),
    (48, "silk_quality_gate.py", ["_check_client_scaffold_leak",
                                  "_check_placeholder_leak"]),
    (49, "silk_narrative.py", ["def evidence_badge_for",
                               "RECONCILED_OUT_TAG"]),
    (49, "silk_render.py", ["def _reconcile_numeric_conflicts"]),
    (50, "silk_reports.py", ["def _client_gap_inputs"]),
    (50, "silk_quality_gate.py", ["_check_gaps_closing_contradiction"]),
    (51, "silk_reports.py", ["def _bidi_isolate_brackets",
                             "def count_suspicious_brackets",
                             "def _pdf_bracket_check"]),
    (51, "tools/rtl_calibration.py", ["def build_bracket_fixture"]),
    (52, "silk_render.py", ["def _already_explained_nearby",
                            "def _year_in_growth_span"]),
    (52, "tests/test_wp6_injector_hardening.py",
     ["test_delivered_sentence_growth_span_year_not_tagged_stale"]),
    (53, "api.py", ["owner_override_required"]),
    (53, "silk_watchdog.py", ["def record_override",
                              "def override_records_for"]),
    (53, "silk_quality_gate.py", ["def run_client_artifact_text_gate"]),
    # البند ٥٤ — بند بثقة 0.0 (خرق حارس المراقبة الحي على demand_trends):
    # ادعاء بثقة صفرية يُعلَن فجوة لا يُشحَن بنداً.
    (54, "silk_llm_runtime.py", ["zero_conf_gaps",
                                 "zero-confidence claim -> declared gap"]),
    (54, "silk_watchdog.py", ["def _check_no_fabrication"]),
    (54, "tests/test_zero_confidence_finding_gap.py",
     ["def test_model_stated_zero_confidence_claim_becomes_declared_gap",
      "def test_inherited_zero_confidence_from_cited_gap_datapoint_not_shipped",
      "def test_watchdog_no_fabrication_holds_on_parse_output_shape"]),
    # البند ٥٥ — تسرّب SILK_HERMETIC الخام بين الاختبارات (لافتة «نموذج
    # توضيحي» في PDF عميل): عازل autouse مضمون الاسترجاع في conftest.
    (55, "tests/conftest.py", ["def _hermetic_env_guard"]),
    (55, "tests/test_wave2_first_pdf_cluster.py",
     ["def test_visual_pdf_lock_production_entrypoint_bare_no_split_no_leaks"]),
    # البند ٥٩ — خمسة إصلاحات MED من تدقيق v2 (الموجة ٢): مصالحة الحجز الفاشل +
    # قياس رؤية الاستقبال + تعطيل أزرار التصدير + مهلة enrich الآمنة + توثيق
    # تماثل /diagnostics؛ الأقفال السلوكية + الحارس الموحّد في السجل.
    (59, "tests/test_wave2_med_fixes.py",
     ["def test_failed_research_run_reconciles_reservation",
      "def test_reaper_also_sweeps_unreconciled_failed_rows",
      "def test_intake_vision_cost_is_usd_metered",
      "def test_export_buttons_disable_during_fetch",
      "def test_enrich_leads_grace_is_proxy_safe_by_default"]),
    (59, "tests/test_regression_registry.py",
     ["def _guard_wave2_med_hardening"]),
    # الهوتفكس (بلاغ قطر × HS 200811، ٢٠٢٦-٠٧-٢٣) — عائلات ٦٠/٦١/٦٢.
    (60, "tests/test_hf_attribution_truncation_plausibility.py",
     ["def test_three_source_finding_yields_three_atomic_references",
      "def test_qatar_fixture_references_gafta_and_gcc_distinctly"]),
    (60, "tests/test_regression_registry.py",
     ["def _guard_composite_source_id_attribution"]),
    (61, "tests/test_hf_attribution_truncation_plausibility.py",
     ["def test_trim_sentence_never_ends_inside_a_number",
      "def test_strip_internal_plumbing_removes_citation_group_no_empty_parens"]),
    (61, "tests/test_regression_registry.py",
     ["def _guard_renderer_truncation_and_empty_parens"]),
    (62, "tests/test_hf_attribution_truncation_plausibility.py",
     ["def test_plausibility_flags_implausible_market_size",
      "def test_build_view_attaches_flags_and_caveat"]),
    (62, "tests/test_regression_registry.py",
     ["def _guard_cross_source_plausibility"]),
    # DEF-2 (التدقيق المعماري ٢٠٢٦-٠٧-٢٤) — عضويةُ الكتلة من مصدرٍ واحد.
    (63, "silk_blocs.py",
     ["EU27", "EFTA", "GCC"]),
    (63, "tests/test_bloc_lists_single_source.py",
     ["def test_eu27_has_all_twenty_seven_members",
      "def test_all_eu_consumers_are_the_single_source_by_identity"]),
    (63, "tests/test_regression_registry.py",
     ["def _guard_bloc_list_single_source"]),
    # DEF-1/G4.1 (التدقيق المعماري ٢٠٢٦-٠٧-٢٤) — مرتكزُ الإنتاج المحليّ.
    (64, "silk_plausibility.py",
     ["_domestic_production_significant", "plausibility_exemptions",
      "guard_relaxed_domestic_producer"]),
    (64, "tests/test_g41_domestic_production_plausibility.py",
     ["def test_producer_market_not_flagged_nigeria",
      "def test_non_producer_market_still_flagged_qatar"]),
    (64, "tests/test_regression_registry.py",
     ["def _guard_g41_domestic_production"]),
    # البند ٦٥ — «قِسِ الرقمَ قبل أن تسأل عنه»: الحوارُ كان يسأل التاجرَ عن
    # عتبةٍ رقمية يُجيب عنها المنتجُ نفسُه (نسبةُ الدهن على العبوة/الويب).
    (65, "silk_hs_attributes.py",
     ["def band_of", "def discriminator", "def select_by_value",
      "def probe_web", "def resolve_by_attribute", "def range_ar"]),
    (65, "silk_hs_resolver.py", ["def official_description"]),
    (65, "silk_hs_confirm.py", ["def resolve_or_probe",
                                "def preflight_resolve"]),
    (65, "silk_render.py", ["def _hs_provenance",
                            "الرمز محدَّد من صورة العبوة",
                            "الرمز محدَّد من مصدر ويب"]),
    (65, "silk_product_intake.py", ["def _sanitize_attributes"]),
    (65, "tests/test_hs_attribute_autoresolve.py",
     ["def test_official_band_parsed_from_reference_not_from_our_seed",
      "def test_no_band_matches_or_wrong_unit_never_fabricates_a_code",
      "def test_web_hit_without_the_product_token_is_rejected_not_borrowed"]),
    (65, "tests/test_hs_attribute_gate_wiring.py",
     ["def test_view_discloses_how_an_auto_resolved_code_was_determined"]),
    (65, "tests/test_regression_registry.py",
     ["def _guard_ask_what_the_product_answers"]),
    # البند ٦٦ — صرامةُ الحدّ تُحمَل من العبارة، والمحورُ الثاني يُرفَض.
    (66, "silk_hs_attributes.py",
     ["_BOUNDS", "_BOUNDS_SUFFIX", "lo_inclusive", "hi_inclusive",
      "def _residual_axis"]),
    (66, "tests/test_hs_attribute_autoresolve.py",
     ["def test_bound_strictness_comes_from_the_matched_phrase",
      "def test_property_every_multiband_heading_is_either_clean_or_refused",
      "def test_second_axis_heading_is_refused_not_resolved"]),
    (66, "tests/test_regression_registry.py",
     ["def _guard_band_boundary_strictness_and_second_axis"]),
    # البند ٦٧ — مصطلحُ البُعد من بياناتٍ لا من شيفرة (عودةُ عائلة ٣٠).
    (67, "data/measurement_dimensions.csv", ["dimension,label_ar"]),
    (67, "silk_hs_attributes.py",
     ["def load_dimensions", "def dimension_terms"]),
    (67, "tests/test_hs_attribute_autoresolve.py",
     ["def test_no_literal_arabic_dimension_term_anywhere_in_the_module",
      "def test_query_construction_has_no_literal_term"]),
    (67, "tests/test_regression_registry.py",
     ["def _guard_dimension_terms_not_frozen_in_code"]),
    # البند ٦٨ (F2) — محورٌ ثانٍ غيرُ رقميّ: تطابقٌ رقميٌّ يجزم خطأً بثقة.
    (68, "silk_hs_attributes.py", ["def _residual_axis", '"axis"']),
    (68, "tests/test_hs_attribute_autoresolve.py",
     ["def test_second_axis_heading_is_refused_not_resolved"]),
    (68, "tests/test_regression_registry.py",
     ["def _guard_multi_axis_heading_confident_wrong_code"]),
    # البند ٦٩ (F3) — أكِّدْ على الأثر المُصيَّر لا على العرض.
    (69, "silk_reports.py", ["def _hs_provenance_sentence"]),
    (69, "tests/test_hs_attribute_gate_wiring.py",
     ["def test_provenance_string_appears_in_the_actually_rendered_document",
      "def test_no_provenance_sentence_when_the_code_was_not_measured"]),
    (69, "tests/test_regression_registry.py",
     ["def _guard_client_operator_document_divergence"]),
    # البند ٧٠ (D1) — صمّامُ ميزةٍ جديدةٍ يُشحَن مُطفأً؛ تفعيلُه قرارُ مالك.
    (70, "silk_hs_attributes.py", ['raw in ("1", "true", "yes", "on")']),
    (70, "tests/test_hs_attribute_autoresolve.py",
     ["def test_flag_is_off_by_default_and_needs_explicit_opt_in"]),
    (70, "tests/test_regression_registry.py",
     ["def _guard_attribute_resolver_flag_off_by_default"]),
    # البند ٧١ (D2) — أساسُ النسبة قربَ الحافّة لا يُحسَم بتحويلٍ تقريبيّ.
    (71, "silk_hs_attributes.py",
     ["def cross_basis_conflict", "def near_any_edge", "_UNIT_BASIS"]),
    (71, "tests/test_hs_attribute_autoresolve.py",
     ["def test_cross_basis_reading_near_a_band_edge_refuses",
      "def test_every_cross_basis_unit_in_the_table_is_guarded"]),
    (71, "tests/test_regression_registry.py",
     ["def _guard_cross_basis_edge_refusal"]),
    # البند ٧٢ (E2) — نصُّ البند من المرجع الرسميّ حصراً، عبر مُصيِّرٍ واحد.
    (72, "silk_hs_dialog.py",
     ["def official_text", "def in_official_reference", "def band_text_ar",
      "def build_candidates"]),
    (72, "tests/test_hs_dialog_official_source.py",
     ["def test_every_multiband_heading_renders_text_faithful_to_the_reference",
      "def test_a_code_absent_from_the_official_reference_is_never_offered",
      "def test_every_user_facing_producer_delegates_to_the_one_renderer"]),
    (72, "tests/test_regression_registry.py",
     ["def _guard_dialog_band_text_from_the_official_reference_only"]),
    # البند ٧٣ (E3) — اكتمالُ أشقّاء المحور شرطُ صحّةٍ لا تفضيلُ عرض.
    (73, "silk_hs_dialog.py", ["def axis_siblings", "def _heading_index"]),
    (73, "tests/test_hs_dialog_official_source.py",
     ["def test_no_axis_group_can_ever_present_a_proper_subset_of_its_siblings",
      "def test_frontend_does_not_truncate_the_candidate_list"]),
    (73, "tests/test_regression_registry.py",
     ["def _guard_dialog_axis_siblings_never_partial"]),
    # البند ٧٤ (E4) — لا صدى لاسم منتجٍ/علامةٍ/دولةٍ في نثرٍ يُقدَّم رسمياً.
    (74, "silk_hs_dialog.py",
     ["def sanitize_prose", "_AR_CLITICS", "def _country_terms"]),
    (74, "tests/test_hs_dialog_official_source.py",
     ["def test_generated_prose_never_echoes_the_product_or_brand",
      "def test_module_has_no_hardcoded_product_brand_or_country_literal"]),
    (74, "tests/test_regression_registry.py",
     ["def _guard_dialog_prose_carries_no_product_brand_or_country"]),
    # البند ٧٦ — قفل المقعد الذرّي **ومُميِّزه**: الحارس يحمي الاثنين، فحذفُ
    # مُوسِّع النافذة يعيد الاختبار إلى «أخضر فارغ» يجتاز كوداً غير ذرّي.
    (76, "silk_platform/users.py",
     ["BEGIN IMMEDIATE", "AND is_active = ?"]),
    (76, "tests/test_platform_concurrency.py",
     ["def _widen_seat_check_window",
      "def test_concurrent_reactivations_never_exceed_seat_cap"]),
    (76, "tests/test_regression_registry.py",
     ["def _guard_seat_lock_is_load_bearing"]),
    # البند ٧٧ — التشخيصُ يحمل السببَ لا الأثرَ: اسمُ المتغيّر المخالف والقاعدة
    # المخروقة، بلا قيمةٍ أبداً (`/health` عامّة).
    (77, "silk_platform/bootstrap.py",
     ["def seed_problem", '"seed_error"']),
    (77, "silk_platform/api.py", ["def platform_root"]),
    (77, "tests/test_platform_bootstrap.py",
     ["def test_a_policy_violating_seed_password_is_named_in_readiness",
      "def test_readiness_never_leaks_the_seed_password_value",
      "def test_the_platform_prefix_leads_to_the_page_not_a_bare_404"]),
    (77, "tests/test_regression_registry.py",
     ["def _guard_readiness_names_the_offending_variable"]),
]


def test_lessons_ledger_and_its_wiring_exist():
    """السجلّ نفسه + وصلاته في CLAUDE.md حاضرة."""
    assert _exists("docs/LESSONS.md"), "docs/LESSONS.md غائب — سجلّ الدروس"
    claude = _read("CLAUDE.md")
    assert "docs/LESSONS.md" in claude, "CLAUDE.md لا يشير إلى LESSONS.md"
    assert "قوانين غير قابلة للكسر" in claude, (
        "قسم القوانين غير القابلة للكسر غائب من CLAUDE.md")


def test_every_symbol_anchor_still_present():
    """كل رمز مصدر مسمّى في عمود الإنفاذ لا يزال موجوداً."""
    missing = []
    for rule, path, needles in _SYMBOL_ANCHORS + _SYMBOL_ANCHORS_EXTRA:
        if not _exists(path):
            missing.append(f"[rule {rule}] ملف مفقود: {path}")
            continue
        src = _read(path)
        for needle in needles:
            if needle not in src:
                missing.append(f"[rule {rule}] {path}: رمز مفقود «{needle}»")
    assert not missing, "أدوات إنفاذ رمزية اختفت:\n" + "\n".join(missing)


def test_every_doc_anchor_still_carries_its_method_markers():
    """البنود الموثَّقة فقط (١، ١٠) — الوثائق موجودة وتحمل علامات منهجها."""
    missing = []
    for rule, path, needles in _DOC_ANCHORS:
        if not _exists(path):
            missing.append(f"[rule {rule}] وثيقة مفقودة: {path}")
            continue
        doc = _read(path)
        for needle in needles:
            if needle not in doc:
                missing.append(f"[rule {rule}] {path}: علامة منهج مفقودة «{needle}»")
    assert not missing, "علامات منهج التدقيق/الدليل اختفت:\n" + "\n".join(missing)


def test_every_named_behavioral_test_still_present():
    """كل اختبار سلوكي مسمّى في السجلّ لا يزال موجوداً (يحمي من الحذف الصامت)."""
    missing = []
    for rule, path, needles in _TEST_ANCHORS:
        if not _exists(path):
            missing.append(f"[rule {rule}] ملف اختبار مفقود: {path}")
            continue
        src = _read(path)
        for needle in needles:
            if needle not in src:
                missing.append(f"[rule {rule}] {path}: اختبار مفقود «{needle}»")
    assert not missing, "اختبارات إنفاذ مسمّاة اختفت:\n" + "\n".join(missing)


def test_all_ledger_rules_are_covered_by_at_least_one_anchor():
    """كل درس في السجلّ له مرساة إنفاذ واحدة على الأقل — لا صفّ بلا حارس.
    عدد الصفوف يُقرأ من docs/LESSONS.md نفسه (أسطر `| N |`) فلا يتخلّف هذا
    الاختبار عن السجلّ عند إضافة درس جديد (بروتوكول التحديث الذاتي)."""
    import re as _re
    ledger = _read("docs/LESSONS.md")
    rows = {int(m.group(1))
            for m in _re.finditer(r"^\|\s*(\d+)\s*\|", ledger, _re.M)}
    assert rows and rows == set(range(1, max(rows) + 1)), (
        f"أرقام صفوف السجلّ غير متتابعة: {sorted(rows)}")
    covered = {r for r, _, _ in _SYMBOL_ANCHORS + _SYMBOL_ANCHORS_EXTRA}
    covered |= {r for r, _, _ in _DOC_ANCHORS}
    covered |= {r for r, _, _ in _TEST_ANCHORS}
    assert covered == rows, (
        f"دروس بلا أي مرساة إنفاذ: {sorted(rows - covered)}؛ "
        f"مراسٍ بلا صفّ في السجلّ: {sorted(covered - rows)}")
