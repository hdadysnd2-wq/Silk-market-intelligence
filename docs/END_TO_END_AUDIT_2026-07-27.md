# END-TO-END AUDIT — 2026-07-27

> **تدقيق للقراءة فقط — لم يُصلَح شيء.** جرد عيوب مرتَّب بالخطورة، مُسنَد كلّه
> إلى `file:line`. «نظيف / غير موجود» تُقال صراحةً حيث فُحصت.
>
> **Read-only audit. Nothing was fixed.** A severity-ordered defect inventory,
> every claim anchored to `file:line`. "Clean / not found" is stated explicitly.
>
> **Evidence classes** (per LAW §2): **direct reproduction** (ran it) ·
> **static code review (file:line)** · **no sufficient evidence — pending**.

---

## Method & what was actually executed

| Check | How | Evidence class |
|---|---|---|
| Hermetic-suite truth | `pytest tests/ -q` under a custom pytest plugin that wraps `socket.socket.connect`/`connect_ex` and records every non-loopback destination | **direct reproduction** |
| Import graph / cycles / orphans | AST walk over all 69 root modules, module-scope imports separated from lazy in-function imports | **direct reproduction** |
| Secret scan | `grep` over tracked tree + `git log -p -S` across all 52 reachable commits | **direct reproduction** (see LIMIT-1) |
| Payload sizes | byte-measured `samples/analysis_latest.json` per top-level key | **direct reproduction** |
| Everything else | line-by-line read | **static code review** |

**LIMIT-1 — the clone is shallow** (`git rev-parse --is-shallow-repository` →
`true`; 52 commits reachable). A full-history secret scan is **not possible from
this sandbox**. The scan below covers the reachable 52 commits only. Classify any
"no keys in history" claim as **no sufficient evidence — pending** for pre-truncation
history. → OWNER-VERIFY: run `gitleaks detect --log-opts=--all` on a full clone.

---

# ■ 1 — THE FOUNDING INVARIANT

## Verdict: **no critical breach found.** The invariant holds.

Every `silk_*_agent.py` and both data layers were read end-to-end. The
`DataPoint(None, source, 0.0, note, today)` failure return is present on **every**
no-key / network-fail / bad-shape / empty-result branch:

| Module | Failure returns audited | Result |
|---|---|---|
| `silk_faostat_agent.py` | :87, :93, :115, :121, :130, :136, :148, :156 | clean |
| `silk_tariffs_agent.py` | :130, :171, :177, :183, :276 | clean |
| `silk_trends_agent.py` | :36, :41, :48, :63, :283, :291, :300, :398 | clean |
| `silk_maps_agent.py` | :37, :41, :47, :64, :70, :91 | clean |
| `silk_websearch_agent.py` | :55, :61, :68, :99, :103, :242, :246, :261, :265, :281 | clean |
| `silk_volza_agent.py` (PAID) | :50, :56, :74, :80 | clean |
| `silk_explee_agent.py` (PAID) | :52, :58, :64, :78, :83 | clean |
| `silk_localprice_agent.py` (PAID) | :104, :109, :115, :127, :131 | clean **but see IMP-1** |
| `silk_gdelt_agent.py` | :46, :75, :85, :96, :100, :115 | clean |
| `silk_google_news_agent.py` | :75, :95, :106, :110, :129, :190 | clean |
| `silk_openalex_agent.py` | :59, :76, :80, :98 | clean |
| `silk_imf_agent.py` | :85, :89, :106, :119 | clean |
| `silk_wto_tariff.py` | :93, :99, :105, :136, :143 | clean |
| `silk_eurostat_agent.py` | :137, :143, :147, :170, :175, :180 | clean |
| `silk_dynamics_agent.py` | :38, :52 | clean |
| `silk_data_layer.py` | :620, :639, :643 (`world_bank`); `comtrade_trade` → `None` on fetch-fail vs `[]` on empty (:503–506) | clean |
| `silk_data_layer_v2.py` | :50, :66, :82, :106; `market_imports` :131–139 | clean |

**Three near-misses that are actually correct, and why** (recorded so a future
reviewer does not re-flag them):

1. **`silk_tariffs_agent.py:101` returns `DataPoint(0.0, ...)`** — a literal zero
   tariff. This is **not** fabrication: `_gcc_documented_exemption` (:60–102) serves
   `0.0` only when *both* parties are GCC members **and** `data/agreements_l1.csv`
   carries a `GCC/member` row, and it is stamped `status="documented_agreement"`
   with the regulation source URL in the note. Any missing condition → `None`
   (:74, :90). Correct: a documented legal exemption is an observation, not a guess.
2. **`silk_market_ranker.py:583` returns `DataPoint(0.0, conf≤0.6)`** for
   "Saudi is not yet a supplier". The zero is *inferred*, the note says so
   explicitly ("غيابٌ مستنتَج من قائمة الموردين المرصودة (قد تكون مبتورة)"), and
   confidence is capped at 0.6. Correct.
3. **`correlation.py:155–156` `... or 0.0`** — I initially flagged this. It is
   correct: `ProductCard.cost_per_unit` is a **required** field (`api.py:252`), so
   the fallback only fires on a genuine user-entered `0.0`; missing shipping is
   disclosed via `shipping_note` (:158–161); and a missing tariff is handled by an
   explicit branch that declares the gap (`correlation.py:206–209`).

## `silk_trend.py` — "year with no data" vs "zero": **correct.**

- `_year_total` (:31–41) drops records whose `primaryValue` is not numeric and
  returns `None` — never sums a missing record as 0.
- `growth_pct` (:44) / `cagr_pct` (:60) filter to `v is not None` **before**
  computing, and return `None` on `<2` observed points or a non-positive base.
- `import_trend` (:76) emits `series[i]["observed"]` per year plus explicit
  `observed_years` / `gap_years` lists, and switches the note to
  "بيانات غير كافية لخط الاتجاه" below 2 points.

`data/`-level equivalent: `silk_data_layer.primary_qty` (:436–450) returns `None`
for zero/negative net weight rather than 0, specifically to avoid a fabricated
unit-price denominator.

---

# ■ 2 — STRUCTURAL DUPLICATION

**Verdict: none of the five pairs is dead. There are zero dead modules in the tree.**

| Pair | Both imported? | Verdict |
|---|---|---|
| `silk_data_layer` ↔ `_v2` | 29 vs 4 module-scope importers | **Not duplication — a layer.** `_v2` imports v1 (`silk_data_layer_v2.py:11–20`) and adds only derived surfaces: PPP (`:88`), competitor shares (`:192`), mirror statistics (`:26`, `:201`), store-first caching (`:315`). Zero overlapping function names. Keep. Rename is the only debt (`_v2` reads as a rewrite; it is an extension). |
| `silk_storage` ↔ `silk_store` | 6 vs 9 importers | **Not duplication — two different databases.** `silk_storage` = the analyses DB (`data/silk.db`): research runs, mission checkpoints, progress, outcomes. `silk_store` = the fact store (`silk_store.db`): indicators, trade flows, settings, HS cache. They share only the *names* `save_analysis`/`get_analysis`/`list_analyses`/`set_outcome` — a genuine naming collision (`silk_store.py:224/252/263/300` vs `silk_storage.py:213/694/666/646`), and the single most dangerous readability trap in the repo. `silk_store.py:10` says the legacy facade "is removed in M5"; M5 has not happened. |
| `silk_quality` ↔ `silk_quality_gate` | 1 vs 3 importers | **Not duplication — different pipelines.** `silk_quality` (120L) flags `/analyze` ranker rows (`silk_engine` only). `silk_quality_gate` (1181L) is the `/research` pre-delivery gate over `view["deep_research"]`. No shared logic. Naming is the only issue. |
| `silk_ai_judge` ↔ `silk_synthesis` | 10 vs 3 importers | **README §9.3 claim VERIFIED.** `ai_verdict` is gone — `silk_ai_judge.py:234` carries the tombstone comment, and `tests/test_wave4_correlation.py:212` asserts `not hasattr(judge, "ai_verdict")`. `synthesize()` (`silk_synthesis.py:90`) is the sole verdict entry. `silk_ai_judge` is now the shared Claude toolkit (`_call`/`_facts`/`_isolate`) + report writer; `silk_synthesis` imports from it (`silk_synthesis.py:26`). No duplication. |
| `silk_hs_classifier` ↔ `_resolver` ↔ `_confirm` | 2 / 5 / 6 importers | **Not duplication — a three-stage ladder.** `_resolver` = deterministic CSV+difflib (offline, free). `_classifier` = one metered Claude call **grounded on the resolver's own candidates**, used only when the resolver fails/is weak; it imports the resolver (`silk_hs_classifier.py`). `_confirm` = semantic gate that flags a resolved-but-wrong code. Each has a distinct trigger. Keep all three. |

### Orphan analysis

Static AST scan reported 4 root modules with zero root-module importers. On
inspection **none is dead**:

- `api.py` — the service entrypoint (60 test/tool importers).
- `silk_evals.py` — CLI eval harness (`__main__` at :729; 5 test/tool importers).
- `fix_agent.py` — dev-only repair agent, wired via `.claude/agents/test-fixer.md`
  + `requirements-dev.txt`. Not runtime. Intentional.
- `silk_competitors_agent.py` — **false positive of static analysis.** It is
  imported by *string* at `silk_engine.py:180–181` (`_enrich_named(..., "silk_competitors_agent", "NamedCompetitorsAgent")`).
  Worth knowing: **three enrichment agents are invisible to any static import tool**
  (competitors, channels, importers — `silk_engine.py:178–189`).

`silk_seed_data.py` is reached lazily at `silk_collectors.py:128`; also live.

---

# ■ 3 — SECURITY

**Verdict: the guard chain is genuinely structural. No exposed keys found in the
reachable tree or history.**

| Check | Finding |
|---|---|
| **Keys in code** | **Clean.** No `sk-ant-*`, `AIza*`, or 32+ hex literal in any tracked `.py`/`.json`/`.md`/`.html`. `.env` is gitignored (`.gitignore:6`); only `.env.example` is tracked. |
| **Keys in history** | **Clean across the reachable 52 commits** (`git log -p -S"sk-ant-"` / `-S"AIzaSy"` → only a skill-doc match, no key material). See **LIMIT-1**. |
| **PAID guard** | **Genuinely structural, no bypass found.** `BaseAgent.run` (`silk_agents.py:157–163`) returns a tagged skipped report **before** `_execute` when `PAID and not deepen_active()`. The contextvar (`silk_context.py:15`) is per-request, correct under FastAPI concurrency. I hunted the obvious bypass — calling the paid *module-level* helpers directly (`volza.importers_by_name:34`, `explee.discover_buyers:41`, `localprice.retail_prices:93`) — and found **zero callers anywhere outside their own file and tests**. Defence in depth also holds: `AnalyzeRequest` (`api.py:259–265`) has no paid fields, so pydantic drops them. |
| **SQL injection** | **Clean.** Exactly one dynamic SQL statement in the tree: `silk_storage.py:188` `f"ALTER TABLE analyses ADD COLUMN {col} TEXT"` — `col` iterates a **hardcoded literal tuple** (:173–187). Not reachable by input. Everything else is `?`-parameterised. See NTH-6. |
| **Prompt injection** | **Solid.** `_isolate` (`silk_ai_judge.py:64–72`) strips the delimiters *out of the payload* before wrapping, so external text cannot break out. Applied consistently on every external field across every prompt builder — product, market, role, agent facts, analyst draft, mission facts, HS description, user chat question, reviewer draft, user agent-commands (`:270`, `:292`, `:369`, `:427`, `:478`, `:552`, `:596`, `:791`, `:829–862`, `:910`, `:1382–1385`). No unisolated interpolation found. |
| **CORS** | **Correct.** `_cors_origins` (`api.py:70–81`) defaults to `[]`, and the middleware is **not mounted at all** unless `CORS_ORIGINS` is set (`api.py:225–228`). `"*"` requires explicit opt-in. `allow_credentials` is never enabled, so a wildcard cannot leak cookies. Plus a CSP/nosniff/Referrer-Policy header middleware (`api.py:230–241`). |
| **429 cap** | **Correct, and TOCTOU-hardened.** `try_reserve_paid_calls` / `try_reserve_usd` (`silk_usage.py:181`, `:255`) do check-and-reserve inside one `BEGIN IMMEDIATE` transaction, so two concurrent `/research` runs cannot both pass the gate. Fail-closed on DB error **iff** a cap is set (`:236`). Post-run `reconcile_usd` (`:240`) swaps the estimate for the token-derived actual; a crashed run keeps its full reservation (deliberately conservative). Separate DB from `silk.db` (`:29`). |
| **Auth coverage** | `_require_key` (`api.py:670–685`) uses `hmac.compare_digest` and is applied to all 30 sensitive endpoints including read paths. Four endpoints are rate-limited only: `/resolve`, `/config`, `/index`, `/markets`, `/sources`, `/research/readiness`. See NTH-4. |
| **Unprotected-keys 503** | `_unprotected_paid_keys` (`api.py:104–117`) → paid path 503s when any provider key is set while `SILK_API_KEY` is unset. Correct. |

---

# ■ 4 — ARCHITECTURE

## Dependency graph (measured)

- **69 root modules**, 32,840 lines. `api.py` alone carries **34 endpoints**.
- **Module-scope import cycles: ZERO.** (Verified by AST over `tree.body` +
  `If`/`Try` blocks only.)
- A naive `ast.walk` scan reports **37 cycles** — *all* of them close through
  **lazy in-function imports**, which is the repo's deliberate ponytail-plugin
  style (`import silk_context` inside `BaseAgent.run:150`, etc.). This is what
  keeps every module importable offline and keyless. **It is load-bearing, not debt.**
- Fan-in: `silk_data_layer` **29**, `silk_agents` **20**, then a long tail ≤3.
- Fan-out: `silk_market_analyst` 5, `silk_engine` 5, `silk_llm_runtime` 4.

The graph is therefore **already a clean two-hub DAG at module scope**. The flat
layout is a *navigability* problem, not a coupling problem.

## Proposed split

```
silk/
  core/     data_layer, data_layer_v2, cache, circuit, context, trace,
            blocs, seed_data, market_resolver, hs_resolver, hs_classifier,
            hs_confirm, market_ranker, engine, discovery, trend, correlation
  agents/   agents (BaseAgent + catalog), + the 18 *_agent modules,
            gmaps, wto_tariff, missions, market_analyst, research,
            llm_runtime, llm_provider, ai_judge, synthesis, decision
  io/       storage, store, usage, collectors, staleness, ops_log, watchdog,
            diagnostics, pricing
  view/     render, reports, narrative, style_contract, quality, quality_gate,
            plausibility, source_coverage
  api.py    (stays at root — the entrypoint)
```

## Migration path that cannot break imports

The **only** safe order, given three dynamic string imports and ~185 test files
that all `import silk_*` by flat name:

1. **Wave A — packages + shims, zero moves.** Create the four packages. Add
   `silk/core/data_layer.py` containing `from silk_data_layer import *  # noqa`.
   Nothing moves; nothing breaks. Land and verify green.
2. **Wave B — invert one leaf at a time.** Move the *file*, and leave the old
   root path as a 2-line re-export shim (`from silk.core.data_layer import *`).
   Start with zero-fan-in leaves (`silk_blocs`, `silk_trend`, `silk_plausibility`),
   end with `silk_data_layer`. One module per PR, suite green each time.
3. **Wave C — fix the three string imports FIRST of all** (`silk_engine.py:178–189`).
   A string-based `importlib` of `"silk_competitors_agent"` will fail silently into
   `_enrich_error_dp` — a moved module here degrades to a *tagged gap* rather than
   an ImportError, which is exactly the kind of silent regression the repo's law
   forbids. Convert to a direct import or a hardcoded registry dict before moving.
4. **Wave D — delete the shims** only after `grep -rn "^import silk_\|^from silk_"`
   over `tests/` + `tools/` returns nothing.

**Do not attempt this in one PR.** With 185 test files and three invisible imports,
a big-bang move has no safe rollback point.

---

# ■ 5 — TESTS

## Hermeticity: **PROVEN, by instrumentation — not by inspection.**

I wrapped `socket.socket.connect`/`connect_ex` in a pytest plugin recording every
non-loopback destination, and ran the whole suite:

```
1875 passed, 20 skipped, 1 warning in 237.38s
===== REAL OUTBOUND SOCKET CONNECTS =====
NONE — suite is network-silent
```

**Evidence class: direct reproduction.** The suite makes zero outbound
connections. `tests/conftest.py:15–48` is the reason it holds: it not only
monkeypatches `socket.socket` but **closes `silk_data_layer._session`** on entry,
because the pooled keep-alive Session could otherwise reuse a live TCP connection
established by an earlier unblocked test and silently bypass the guard.

Note: 58 test files contain no network-blocking idiom at all. I checked a sample —
these are pure-function tests (render, sanitizer, style-gate, CSV reference) that
never reach a fetch path. Not a hermeticity gap.

## Coverage

- **Modules never named in `tests/`: 2** — `fix_agent` (dev tool) and
  `silk_seed_data`. `silk_seed_data` is a live fallback path
  (`silk_collectors.py:128–143`) that supplies population/GDP when the World Bank
  is unreachable — **an untested production fallback** (see NTH-7).
- **Thin (<5 mentions):** `silk_product_intake` (2), `silk_importers_agent` (4),
  `silk_openalex_agent` (4), `silk_prerun` (4), `silk_profiles` (4).

## Is there a test proving no-fabrication when each source fails? **Yes.**

369 `is None` assertions across `tests/`. Per-source, counting files that both name
the module **and** cut the network/patch the fetch: `silk_data_layer` 66,
`silk_data_layer_v2` 15, `silk_tariffs_agent` 11, `silk_websearch_agent` 10,
`silk_maps_agent` 9, `silk_trend` 9, `silk_trends_agent` 8, `silk_faostat_agent` 4,
`silk_volza_agent` 4, `silk_gdelt_agent` 4, `silk_explee_agent` 3,
`silk_localprice_agent` 3, `silk_eurostat_agent` 3, `silk_imf_agent` 2,
`silk_wto_tariff` 2, **`silk_google_news_agent` 1, `silk_openalex_agent` 1**.
The last two are the thinnest guards on the invariant.

---

# ■ 6 — PERFORMANCE & OPS

- **Sequential fan-out** — see IMP-6. Only 2 of ~13 fan-out sites are parallel.
- **`silk_cache` effectiveness** — the mechanism is sound (sha1 of url+sorted
  params, per-source TTL: closed Comtrade years 30d / current 24h
  `silk_data_layer.py:497`, World Bank 7d `:614`). Two gaps: **no eviction at all**
  (IMP-4), and **failures are not negatively cached** (`silk_cache.py:105` returns
  `None` without writing), so a persistently-down source is re-hit on every request
  through the full timeout.
- **Payload sizes** — see IMP-7. Measured, not estimated.
- **`railway.json` volume correctness** — see CRIT-1.

---

# FINDINGS, ORDERED BY SEVERITY

## CRITICAL

### CRIT-1 — Persistence is not config-as-code; the LESSONS §4 data-loss guard ships OFF
**`railway.json:1–14`** (whole file) · **`api.py:171–199`** · **`README.md:178`**

`railway.json` declares builder, start command, healthcheck and restart policy —
and **no volume, no `SILK_DATA_DIR`, no `SILK_REQUIRE_PERSISTENT_DATA_DIR`.** The
boot trap that is supposed to make this failure loud (`api.py:171–199`) is
**opt-in**: it only fires when `SILK_REQUIRE_PERSISTENT_DATA_DIR` is truthy, and
nothing in the repo sets it. `README.md:178` moves the entire protection into a
**manual dashboard step**.

**Why it matters:** this is precisely the incident LESSONS §4 records — paid
analyses written to an ephemeral container disk and destroyed on the next
redeploy. The guard exists and is well built (it even checks `is_mount` and
writability, `api.py:174–199`), but the deployment contract in the repo cannot
prove it is armed. Everything routes through `SILK_DATA_DIR`
(`silk_usage.py:36–39`, `silk_cache.py:33–35`, `silk_storage._db_path`), so one
missing dashboard variable silently loses **all four** stores at once.

**Evidence class:** static code review. The live Railway env is **not verifiable
from this sandbox** → the actual production state is **no sufficient evidence —
pending**, OWNER-VERIFY via `GET /health` (it exposes the resolved paths).

**Proposed fix:** put the contract in the repo — add to `railway.json`:
```json
"deploy": { "envVars": { "SILK_DATA_DIR": "/data",
                         "SILK_REQUIRE_PERSISTENT_DATA_DIR": "1" } }
```
(or a `volumes` block if the Railway schema version in use supports it), so a
misconfigured deploy **fails the healthcheck loudly** instead of writing to
ephemeral disk. Ship a rung-2 test asserting `railway.json` carries both keys.

---

## IMPORTANT

### IMP-1 — `suggest_price` substitutes 0% for a missing tariff, then claims it applied one (and is dead code)
**`silk_localprice_agent.py:245–246`**

```python
floor = float(cost_per_unit) * (1.0 + float(tariff_pct or 0) / 100.0) \
    + float(shipping_per_unit or 0)
```

`tariff_pct` arrives from `applied_tariff`, which returns `None` on a declared
WITS gap (`silk_tariffs_agent.py:171/177/183`) — the normal case for
un-reported pairs. `or 0` silently turns that declared gap into **0% duty**. The
floor then propagates into `suggested_min`, `suggested_max`,
`margin_at_min_pct`, `margin_at_max_pct` (`:286–291`) as real numbers, and the
rationale string (`:258–260`) asserts *"التكلفة × (1 + التعريفة%) + الشحن — من
مدخلاتك"* — telling the reader a tariff **was** applied. Margins are overstated;
`landed_cost_floor` is understated. This is exactly the "a zero that reads as
real data" pattern the founding principle forbids. (Contrast `correlation.py:206–209`,
which handles the identical situation **correctly** — same repo, same input, two
different answers.)

**Compounding: the function has zero production callers.** `grep` over the whole
tree outside `tests/` finds only the definition; no `_execute` path
(`silk_localprice_agent.py:307–322`), no render key, no API field ever emits
`suggested_min`/`landed_cost_floor`. It is a shipped-but-unwired feature (P1-6)
with one test file. So the defect is **latent, not live** — which is why this is
IMPORTANT, not CRITICAL.

**Proposed fix:** (a) when `tariff_pct is None`, either return the floor as `None`
or compute it and add an explicit gap string to the rationale, mirroring
`correlation.py:207–209`; same for `shipping_per_unit`. (b) Decide the feature's
fate — wire it or delete it; a dead money-path function is worse than either.

### IMP-2 — A stored fact with confidence 0.0 is re-served at 0.9
**`silk_engine.py:351`** · **`silk_missions.py:457`** · **`silk_research.py:942, :1051, :1188`**

```python
float(got.get("confidence") or 0.9)
```

`or` fires on `0.0` as well as on `None`, because `0.0` is falsy. Any indicator row
in the fact store carrying a genuine zero confidence is **promoted to 0.9** on
read — the exact inversion of the invariant, applied to the confidence field
rather than the value. Live writers currently store 0.85/0.9/`_WB_CONF`
(`silk_collectors.py:107, :142`; `silk_trends_agent.py:99` passes through
`live.confidence`, which is only non-zero on the success branch), so this is
**latent today** — but the store is durable, shared, and now written by tools
(`tools/backtest.py:60`, `tools/stage2c_proof.py:76–87`), so one bad row poisons
four read sites at once.

**Proposed fix:** replace with an explicit None-check —
`float(got["confidence"]) if got.get("confidence") is not None else 0.9` — at all
five sites, and add a lock test asserting a 0.0-confidence store row survives the
round-trip as 0.0.

### IMP-3 — Data scarcity inflates a market's rank (single-market component → perfect score)
**`silk_market_ranker.py:594–609`**

```python
lo, hi = min(vals), max(vals)
if hi == lo:
    return 1.0
```

When exactly one market has a value for a component (or all are equal),
`_normalize` awards **1.0 — a perfect component score** — and that flows straight
into `total_score` (`:719–724`). A market that is the *only* one with, say, tariff
data scores full marks on it **because the others are missing**, not because it is
better. The comment at `:597–600` documents this honestly, and row confidence drops
via `present / len(WEIGHTS)` (`:728`) — but confidence and score are **separate
fields**, and the ranking (`:754`) sorts on score first. `silk_quality.validate_market_row`
(`silk_quality.py:28–66`) does **not** flag it.

**Proposed fix:** don't change the maths (the comment is right that it would flip
existing rankings). Instead emit a flag: when a component's `raw_tables[name]` has
`len(...) == 1`, add `"component X scored 1.0 on a single-market denominator"` to
`quality_flags`, so the distortion reaches the consumer.

### IMP-4 — `silk_cache` has no eviction; it can fill the same volume that holds `silk.db`
**`silk_cache.py:96–105`** (write path) · **`silk_cache.py:23–38`** (`_cache_dir`)

Every distinct `(url, sorted params)` writes one JSON file forever. There is no
size cap, no LRU, no TTL-based deletion (TTL only decides *freshness on read*,
`:74–77`; stale files are never removed). With `SILK_DATA_DIR=/data` the cache
lands on the **same mounted volume** as `silk.db`, `silk_store.db` and `usage.db`
(`:33–35`). Comtrade keys on (hs × market × year × flow × partner) across ~38
markets — the file count grows monotonically with usage.

**Why it matters:** a full volume does not degrade gracefully. SQLite writes to
the analyses DB start failing, and `silk_storage`'s write paths are wrapped in
best-effort try/except — so the first symptom is **silently lost analyses**, the
same end state as CRIT-1 by a different route.

**Proposed fix:** a bounded sweep on write (delete files older than `max(TTL)`
when the directory exceeds N files or M bytes), plus expose cache bytes in
`GET /health` next to the resolved paths.

### IMP-5 — The test suite rewrites committed sample artifacts
**`tests/test_wave11_identity_and_hardening.py:253`**

```python
ns = runpy.run_path(os.path.join(_root(), "tools", "gen_research_sample.py"))
```

`tools/gen_research_sample.py:317, :324` writes `samples/research_report_latest.docx`
and `.md`. Running `pytest tests/ -q` therefore **dirties the working tree**
(reproduced: `git status` showed both files modified, the `.md` diff being the
date line `2026-07-23` → `2026-07-27`).

**Why it matters:** house rule §10.6 says every render-layer change must regenerate
the committed samples — but here the samples get regenerated as a **side effect of
running tests**, which (a) makes "is the sample current?" untrackable, (b) leaves
CI with a dirty tree, and (c) means a developer can commit a sample they never
looked at.

**Proposed fix:** have the test run the generator into a `tmp_path` (pass an output
dir), and keep sample regeneration an explicit, deliberate `tools/` invocation.

### IMP-6 — 11 enrichment layers run sequentially, each looping markets sequentially
**`silk_engine.py:164–196`** (layer dispatch) · e.g. **`:427–437`** (trends), **`:441–455`** (tariffs), **`:458–470`** (faostat) · **`:157–162`** (core agents)

The layer dispatch is a straight-line `if with_x: _enrich_x(...)` sequence, and
each `_enrich_*` is `for row in rows:` with a blocking HTTP call per row. Core
agents are the same: `for row in ranked[:_ENRICH_TOP]` (`:157`) calling
`manager.distribute` (`silk_agents.py:424–440`), which itself runs its agents in a
sequential `for agent in self.agents`. Only **two** sites are parallel:
`_enrich_research` (`:422–424`, `ThreadPoolExecutor`) and `rank_markets`
(`silk_market_ranker.py:698`).

**Why it matters:** with 3 top markets, a fully-enriched `/analyze` is ~33 serial
blocking round-trips *after* ranking — and `silk_data_layer._throttle` enforces a
**1100 ms minimum gap per Comtrade host call** (`silk_data_layer.py:97–118`), so
the serial structure and the throttle multiply. Every layer is independent (each
writes a distinct `row[key]`), so this is latency paid for nothing.

**Proposed fix:** the layers are already shaped for it — run the independent
`_enrich_*` calls in one `ThreadPoolExecutor` (they touch disjoint row keys), and
parallelise `ResearchManager.distribute` across its 3 agents. Keep the
per-host throttle as the real rate governor.

### IMP-7 — `/analyze` ships the same data twice; `/analyses` has no LIMIT
**measured on `samples/analysis_latest.json`** · **`silk_storage.py:679–682`**

Byte-measured breakdown of a real response (115,501 bytes total):

| key | bytes |
|---|---|
| `markets` | 56,954 |
| `view` | 57,254 |
| everything else | ~1,300 |

`view` is **derived from** `markets` by `silk_render.build_view` — the response
carries both, so ~50% of the payload is a re-serialisation of data already present.
(The canonical-view rule is right; shipping *both* to the client is the waste.)

Separately, `list_analyses` (`silk_storage.py:679–682`) is
`SELECT ... ORDER BY id DESC` with **no LIMIT and no pagination**, and
`GET /analyses` (`api.py:2431`) returns all of it. The history sidebar payload
grows without bound.

**Proposed fix:** (a) let the client request one representation —
`?include=view|markets|both`, defaulting to `view` (the frontend already consumes
`result.view`, `web/index.html`); (b) add `limit`/`after_id` to `list_analyses` —
the signature already hints at it via `silk_store.list_analyses(limit, after_id)`
(`silk_store.py:263`), so mirror that.

### IMP-8 — Three god-modules concentrate 30% of the tree
**`silk_reports.py` (4,504L)** · **`api.py` (2,997L, 34 endpoints)** · **`silk_render.py` (2,236L)**

Combined 9,737 of 32,840 lines. `api.py` mixes routing, auth, the paid-guard chain,
the readiness panel, the research pipeline orchestration (`_run_research_pipeline`
at `:1357`, `_research_background` at `:1608`) and the client-export gate. This is
the practical blocker for the §4 split: three files that must each be decomposed
*before* the package move is worth doing.

**Proposed fix:** sequence it after §4 Wave A — extract `api.py`'s pipeline
orchestration into `silk_pipeline.py` and its guard chain into `silk_guards.py`
first; these are the two cleanly separable seams.

---

## NICE-TO-HAVE

### NTH-1 — README understates the test count by 21×
**`README.md:206`** claims *"اختبارات الدخان الهيرمتيكية: **87**"*. Measured:
**1,895 collected / 1,875 passing** across 185 files. Fix the number, or replace it
with "count is live in `tests/`" (which the same sentence already says — so the
literal 87 is pure stale noise).

### NTH-2 — Defensive gap: HHI 0.0 at confidence 0.9 when every supplier row lacks a share
**`silk_market_ranker.py:585–590`**
```python
shares = [c.value.get("share") for c in comps if c.value]
hhi = sum((s / 100.0) ** 2 for s in shares if s is not None)
```
If `comps` is non-empty but every `share` is `None`, `hhi` is `0.0` — the *best
possible* competition score (perfectly fragmented market) — returned at confidence
0.9 with a note claiming "supplier HHI over N suppliers". **Currently unreachable**:
`_competitor_dp` (`silk_data_layer_v2.py:105`) always computes a numeric share, on
both the live and mirror paths. But the sibling function two lines up
(`_saudi_position_component:576–579`) *does* guard the same condition explicitly —
so the asymmetry is an accident, not a decision. **Fix:** return
`DataPoint(None, ..., 0.0, "supplier rows carry no share values")` when
`not any(s is not None for s in shares)`.

### NTH-3 — Fabricated 0.0 margin on a zero-priced listing
**`correlation.py:136–138`** — `_margin_pct` returns `0.0` when `price` is falsy.
The caller (`:201–204`) only checks that `float(obs["value"])` *parses*, not that
it is positive, so a listing priced 0 yields a real-looking `margin_at_match_pct: 0.0`.
**Fix:** return `None` and let the thread declare the gap, consistent with the
module's own discipline elsewhere.

### NTH-4 — `/research/readiness` runs unauthenticated compute
**`api.py:1746–1754`** — rate-limited but **not** `_require_key`-guarded, while it
resolves HS codes (`:1756–1758`) and runs `_readiness_checks` (`:1631`), which calls
`confirm_hs` and coverage lookups. No spend, no secrets returned — hence
nice-to-have — but it is the only free-compute surface reachable without a key when
`SILK_API_KEY` is set. **Fix:** add `_require_key(request)` for symmetry with
`/classify_hs` (`:613`), which is guarded.

### NTH-5 — Full-history secret scan not possible here
See **LIMIT-1**. **Fix:** run `gitleaks`/`trufflehog` on a full clone in CI.

### NTH-6 — The one dynamic SQL statement deserves a lock test
**`silk_storage.py:188`** — safe today (hardcoded tuple at `:173–187`), but it is
the single f-string SQL in 32k lines. **Fix:** an AST test asserting no
`execute(f"...")` outside this one allow-listed line.

### NTH-7 — `silk_seed_data` is an untested production fallback
Never named in `tests/`, yet it supplies population/GDP when the World Bank is
unreachable (`silk_collectors.py:128–143`). A silent regression there degrades data
quality with no alarm. **Fix:** one hermetic test asserting the seed snapshot
returns real values with `source`/year, and `None` for an absent country.

### NTH-8 — Naming collisions cost readers real time
`silk_storage` ↔ `silk_store` share four function names for **different databases**
(§2). `silk_quality` ↔ `silk_quality_gate` serve different pipelines.
`silk_data_layer_v2` is an extension, not a version. **Fix:** as part of §4 Wave B,
rename to `analyses_db` / `fact_store`, `row_quality` / `delivery_gate`, and
`data_layer_derived` — with shims, one per PR.

---

# SUMMARY

| Axis | Verdict |
|---|---|
| **1 — Founding invariant** | **Holds. No critical breach.** 17 modules audited branch-by-branch. Two latent confidence/zero-substitution defects (IMP-1, IMP-2), both currently unreachable in production. `silk_trend.py` correctly distinguishes gap from zero. |
| **2 — Duplication** | **No pair is dead; no module is dead.** All five "duplicates" are genuine layers or different pipelines. The problem is *naming*, not redundancy. |
| **3 — Security** | **Clean.** No keys in code or reachable history. The PAID guard is structural with no bypass found. SQL, prompt-isolation, CORS, and the atomic 429/USD caps are all correct. |
| **4 — Architecture** | **Zero module-scope cycles** — the graph is already a clean DAG. The 37 apparent cycles are deliberate lazy imports. Flat layout is a navigability cost; split proposed, migration must be incremental and must fix three string-imports first. |
| **5 — Tests** | **Hermeticity proven by instrumentation**, not asserted: 1,875 pass with **zero outbound sockets**. Per-source no-fabrication guards exist for every source; two are thin. Two modules untested. |
| **6 — Perf & ops** | Sequential fan-out (IMP-6), unbounded cache (IMP-4), 2× payload (IMP-7), and the persistence contract missing from `railway.json` (CRIT-1). |

**One-line takeaway:** the invariant this system is built on is genuinely
enforced — the real risks are **operational** (persistence not in config-as-code,
unbounded cache) and **latent** (two dead-or-unreachable code paths that would
fabricate the moment they were wired up).
