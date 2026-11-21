# Experiment: how many Claude tokens does Qwen delegation save?

Question: for the same task, how much Claude usage does "Sonnet 5.5 (medium) does everything" use compared with "Sonnet 5.5 (medium) specs and reviews, local Qwen implements"?

## Why this is measurable
`claude -p ... --model claude-sonnet-5-5 --effort medium --output-format json` prints exact usage per run: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `total_cost_usd`, `num_turns`, `duration_ms`. Each arm runs as its own headless session, so totals are not mixed with this conversation. `--max-budget-usd` caps spend per run. (Checked: a one-word prompt reports ~27K cached input tokens of fixed overhead from the system prompt and tools; it applies to both arms and is reported separately.)

## Design
- **Same starting point:** each task has a base commit containing a spec and the failing tests, written once by hand (shared cost, not counted). Every run gets a fresh git worktree from that commit.
- **Arm A (Claude alone):** headless Sonnet 5.5 medium, told to make the test command pass without editing tests.
- **Arm B (Claude + Qwen):** headless Sonnet 5.5 medium, told **not to write implementation code itself**: write a task file, run `implement.py`, review the diff, apply it, and fix only if it fails. Qwen's tokens are logged separately (they are free).
- **Runs:** 3 per arm per task, because LLM runs vary. Report the median and the range, not a single run.
- **Tasks (different kinds, since the answer depends on kind):**
  1. **T-logic:** F1, kind-aware RED in `get_service_catalog` (hard to get right, test-heavy).
  2. **T-mechanical:** two Kubernetes manifests (kgateway Gateway + HTTPRoute, an Istio AuthorizationPolicy) copying an existing pattern (the bulk of demo work).
  3. **T-ui (optional):** node shapes and badges in `serviceMapUtils.js`, with `node --test`.

## What we record per run
| Metric | Source |
|---|---|
| Claude input / cached / output tokens, cost, turns, wall time | headless JSON |
| Qwen prompt and completion tokens, rounds, seconds | `~/.local/state/local-delegate/implement.jsonl` |
| Passed? | harness re-runs the test command independently in the arm's worktree |
| Regressions | the relevant existing suite (`./scripts/test-all.sh --only ...`) |
| Claude took over the code (arm B)? | files edited by Claude vs by `delegate` |
| Diff size and a quick read for quality | `git diff --stat`, review notes |

## Result we report
- **Savings** = (arm A Claude cost) - (arm B Claude cost), as tokens by type and as `total_cost_usd`, per task, with range.
- **Cost of quality:** pass rate, rounds, how often arm B escalated, and any review fixes.
- **Break-even:** the task kinds where B saves a lot (expected: mechanical), a little, or loses (expected risk: small tasks, where spec-writing and review cost about as much as writing the code).
- **Wall time** for both arms (Qwen is free but may be slower or faster end to end).

## Caveats to state with the numbers
- Sample is small (about 6 runs per task); this is an estimate, not a benchmark.
- Writing the tests is outside both arms. In real use Claude pays for it either way, so true savings are slightly smaller than measured.
- Costs include cache reads, which are cheap; compare `total_cost_usd` as well as raw tokens.
- Prices and caching behaviour can change; report the date.

## Cost and effort
Pilot cost was about $0.23 for four runs. A full 3 runs per arm per task is about $1; each run is capped by `--budget`.

## Run it
```
cd /Volumes/external/code/inferencerouter/delegate/bench
python3 bench.py check t-logic            # base fails, gold passes (no model calls)
python3 bench.py run t-logic A -n 3       # arm A, 3 runs
python3 bench.py run t-logic B -n 3       # arm B, 3 runs (needs the Ollama host online)
python3 bench.py grade <run-id> 0|1|2 "note"   # after reading /tmp/bench/<run-id>.diff
python3 bench.py report
```
Tasks live in `bench/tasks/<id>/` (`overlay/` = spec + tests, `task.json`, gold solution). Base is tinyolly commit `146e633`; runs use throwaway detached worktrees, so tinyolly's branches are untouched.

## Quality bar
A run counts as passed only if the done-command passes on an independent re-run, no protected test file changed and no file outside "May change" changed. Regression passing and a manual grade (0-2) are reported next to it.

## Pilot results (2026-11-14, n=1 per cell, not conclusions)
| Task | A cost | B cost | Saved | Notes |
|---|---|---|---|---|
| t-mechanical | $0.050 | $0.049 | 2% | Tiny task; fixed overhead dominates; both pass |
| t-logic | $0.084 | $0.044 | 47% | B: Qwen passed in 2 rounds (52K Qwen tokens), diff correct but wordier (grade 1); A grade 2 |
Observation: arm B finished in 2 turns, so Claude reviewed from the delegate output and diff only. Watch whether reviews catch real problems.

## Full set (2026-11-14, n=3 per cell, all passed, 0 regressions)
| Task | A cost | B cost | Saved | Notes |
|---|---|---|---|---|
| t-logic | $0.085 | $0.044 | 48% | B diffs 99-130 lines vs A 61; Qwen 44-122s; Claude never hand-edited |
| t-mechanical | $0.050 | $0.049 | 3% | Fixed overhead dominates |
Quality grades exist only for the first run per arm; grade the rest before quoting quality.
