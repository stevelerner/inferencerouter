# Improvement log
One entry per surprise. Format: date, task, symptom, cause, change, result of re-run.

## 2026-11-14
- **t-logic B (pilot):** Qwen's code was correct but verbose (7 extra state keys vs a per-service span list). Cause: spec did not name a simple approach. Try next: add "keep one list of spans per service and filter it" to Constraints; re-run and compare diff size and grade.
- **t-mechanical:** B saved ~2%. Cause: task too small, fixed ~77K cached tokens dominate. Change: routing rule now says delegate only when Claude would write >30 lines or touch 2+ files... this task touched 2 files but only ~40 lines; threshold may need "or 100+ lines". Revisit after more runs.
- **harness:** `claude -p` prints a warning line before the JSON; parse from the last line starting with `{`. Fixed.

## 2026-11-14, full set (n=3 per cell)
- **t-logic:** A $0.085 vs B $0.044 (48% saved), 3/3 pass both. B diffs are 99-130 lines vs A's 61; only the first of each arm was graded. Qwen wall time 44-122s vs 12s. Next change: add the "one list of spans per service, filter it" hint to Constraints, re-run B x3, compare diff size.
- **t-mechanical:** A $0.050 vs B $0.049 (3%). Not worth delegating at this size; routing rule stands.

## 2026-11-14, spec-hint re-run (t-logic-hint, B x3)
- Adding one "how" line to Constraints (one list of per-span tuples, filter once, no parallel counters) cut Qwen's diffs from 99-130 to 72-75 lines (A: 61), all graded 2. Claude cost $0.045-0.054 (two runs used 4 turns instead of 2, so ~$0.053 median), still ~38% under A's $0.085.
- Lesson: when Qwen's output is correct but bloated, the fix is a one-line implementation hint in the spec, not more review. Default packet template should include a "Shape" line. Minor: Qwen still adds a restating comment despite implement-rules.md.

## 2026-11-14, first real delegation: F1 kind-aware RED (TinyOlly `get_service_catalog`)
- Spec = t-logic-hint packet (with Shape line), `--in-place` on tinyolly main. Qwen: 1 round, 21.8K prompt + 12.3K completion tokens, 94s. Claude wrote no implementation code; reviewed the 79-line diff, ran the common and UI suites (43 pass each). Grade 2. Uncommitted in tinyolly: `storage_sqlite.py` change plus `tests/test_catalog_kind.py`.
- Claude cost for this task: spec reuse (already written for the bench) + one review. Real-work log now started in `~/.local/state/local-delegate/implement.jsonl`.
- Not yet verified live: the running UI image predates the change; rebuild (`k8s/04-rebuild+deploy-ui.sh`) before checking the map in the browser.

## 2026-11-14, F4 edge RED and scenario manifests (real work)
- **F4 (`get_service_graph`):** Qwen 2 rounds, 44.6K+2.7K tokens, 26s; 135-line cumulative diff (43 insertions in the F4 part), shape followed (per-edge span tuples). Common 49 and UI 43 tests pass. Grade 2. Round 1 failed once; the failing-output loop fixed it.
- **Scenarios (4 YAML + `03-scenario.sh`):** Qwen 1 round, 3K+1K tokens, 8s. Applied live: all four accepted by the cluster, shop kept working under `authz`. Claude wrote the spec and `check_scenarios.py` (~80 lines), which is as long as the output: for this task type spec cost ~ code cost, so token savings are small (same pattern as t-mechanical). The value was the independent check, not saved tokens.
- **Defect (fixed 2026-11-14 in `implement.py`: files starting with `#!` are now chmod +x):** Qwen writes shell scripts without the executable bit. Was: add to `implement-rules.md`/task template: "Claude runs chmod +x on new scripts" or have implement.py set +x for files starting with a shebang.

## 2026-11-14, tooling moved
- `implement.py`, the bench harness, rules, task template and these method docs now live in `inferencerouter/delegate/`. Config uses the shared `LOCAL_BASE` and `LOCAL_MODEL` (no built-in default model or host). Real-work log: `~/.local/state/local-delegate/implement.jsonl`. Added `test_implement.py` (11 tests, fake Ollama).
