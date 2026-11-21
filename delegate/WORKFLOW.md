# Working strategy: Claude plans and reviews, local Qwen builds

Goal: spend Claude tokens on judgment (design, specs, review, debugging), and let the local `qwen3.8:27b-mtp-q4_K_M` on a local GPU host (RTX 5090, ~118 tok/s) write most of the code.

## The rule
**Claude writes the spec and the test. Qwen writes the code until the test passes. Claude reads the diff.**
Output tokens are the expensive part; reading a 100-line diff costs far less than writing it. A passing test is the check, so Claude does not re-verify by hand.

## Who does what
| Claude | Qwen |
|---|---|
| Architecture, plans, task specs, acceptance tests | Implementation against a spec and a failing test |
| First-of-a-kind code that sets a pattern (one example per kind) | Copies of that pattern: more manifests, scripts, tests, MCP tools, UI shapes |
| Subtle logic: graph builder semantics (F1, F3, F4, F5), merge rules | Boilerplate: k8s/Helm YAML, Gateway API routes, Istio policies, `0x-*.sh` scripts, fixtures |
| Live debugging (CNI, ztunnel, collector, kind) and design calls | Log and OTLP-capture summarising: "list the attributes ztunnel emits", so Claude reads 30 lines, not 30,000 |
| Review, integration, commits, final docs voice | Docs first drafts, README/docs sections, unit tests from a written case list |

Do **not** delegate: anything needing live cluster iteration, ambiguous requirements, or the T0 interpretation of what the proxies really emit.

## Mechanism (recommended, in order)
1. **`delegate` script** (about 40 lines, in `ai/harnesses/` or `scripts/`). Input: a task file. It creates a git worktree on branch `qwen/<task>`, calls Ollama `/api/chat` with `think:false` (mechanical edits; see below), writes the files, runs the task's test command, feeds failures back for up to 3 rounds, then prints only: diffstat, test result, and the last failure if any. Claude reviews that diff.
2. **If one-shot is too weak for multi-file work:** drive Qwen through an agent loop instead of a single call, in the same worktree. Options, simplest first: a second Claude Code session whose backend is Ollama (Ollama offers an Anthropic-compatible endpoint; verify it works with your version), `aider`, or the Zed agent you already have configured. Claude still only specs and reviews.
3. **Later, optional:** wrap `delegate` as an MCP tool so any client can hand work to Qwen.

## Task packet (what Claude writes, keep under ~1 page)
- **Goal** in two sentences.
- **Read:** explicit file paths (and one pattern file to imitate). No "explore the repo".
- **May change:** an allow-list of paths. Anything else in the diff fails the task.
- **Constraints:** a short `implement-rules.md` for Qwen distilled from CLAUDE.md and the project rules (functionality over security, front matter on docs, MCP-only skills, no CI tests).
- **Done when:** one command (`pytest path`, `node --test`, `./scripts/test-all.sh --only ...`, `kubectl apply --dry-run=server`).

## Guardrails that come from your box
- **Context cliff:** Ollama silently truncates past `num_ctx` and drops the *front* (system prompt included). Keep packets well under 100K tokens and set `num_ctx` per request. Never let a prompt overflow.
- **Thinking:** `qwen3.8` is a thinking model. Use `think:false` for edits and short answers; `low` or default for design-heavy tasks; never `high`/`max` in a loop (4x slower, no gain measured).
- **One GPU:** run one delegated job at a time. Parallel jobs fight over VRAM and the 196K window needs the card to itself.
- **Network:** the Ollama host (`LOCAL_BASE`) must be reachable from this machine; check with `curl $LOCAL_BASE/api/tags`.
- **Escalate:** if tests still fail after 3 rounds, the diff leaves the allow-list, or Qwen invents an API, Claude takes over the task. Log it.

## Pilot (do this before committing to the approach)
1. Reachability and a latency smoke test; confirm the model is loaded with the 131072 or 196608 window.
2. Build `delegate`; write `implement-rules.md` for Qwen.
3. Pilot task, low risk and well specified: **T1 / F1 kind-aware RED**. Claude writes the failing unit tests (golden spans with proxy CLIENT and SERVER pairs). Qwen implements. Claude reviews the diff.
4. Measure: Claude tokens used vs a normal run, rounds to green, review fixes needed. Runs log to `~/.local/state/local-delegate/implement.jsonl` (real work) and `bench/results.jsonl` (experiment). Expand only if the pilot shows real savings and a low rework rate.

## Mapping to the plans
| Work | Claude | Qwen |
|---|---|---|
| Demo Phase 0 spike | All of it (live debugging) | Summarise captured OTLP and logs |
| Demo Phase 1-2 (manifests, scripts, scenarios) | First manifest and script per kind, scenario specs | The rest of the YAML, scripts, policies |
| Demo Phase 3 (docs, tests) | Outline, tone, final pass | Drafts, `scripts/test-istio.sh` assertions from a list |
| T1 F1, F4 | Tests and review | Implementation |
| T2 F2, F3 | Rules-file design, merge semantics, tests | Rule presets, UI shapes and badges, JS tests |
| T3 F5, F7 | Adapter design | Mapping tables, endpoint and card boilerplate |
| T4-T5 F6, F8, F9, F10 | Review, trace-view design | Most UI and API code |
| T6 F11, F12 | Preset design | Preset YAML, MCP tool additions, docs |

## Success measure
Claude spends tokens mainly on specs, tests, review and debugging. If rework rate climbs (Claude rewriting Qwen's output), tighten specs or pull that task type back to Claude.

## Improvement loop (how the method gets better)
Every delegated task and every bench run ends with one entry in `IMPROVEMENTS.md`, only when something went wrong or surprised us:

| Symptom | Likely cause | Change to make |
|---|---|---|
| Qwen passes tests but diff is verbose or off-pattern | Spec said what, not how | Add a "Pattern" file to Read, or a style line to Constraints |
| Qwen needs 3 rounds | Test failure output unclear, or spec has two readings | Sharpen the failing assertion messages; state the formula in Constraints |
| Qwen edits outside the allow-list or invents an API | Missing context | Add the file to Read; add the rule to `implement-rules.md` |
| Claude hand-edits after delegate | Task too fiddly for the model | Mark the task kind "Claude" in the routing table; split it smaller |
| B costs the same as A | Task too small; fixed overhead dominates | Raise the routing threshold (see Routing rule) |
| Passes but a grade of 0-1 | Tests too weak | Add the missed case to the tests, re-run |

Cadence: after each batch of 3+ tasks, run `bench.py report`, read the log, change one thing (template, `implement-rules.md`, routing rule, num_ctx, model), and re-run the same task to see if it moved. One change at a time, so cause is clear.

## Routing rule (update from data)
Delegate when the task has a runnable check and Claude would otherwise write more than ~30 lines or touch 2+ files. Do it directly when it is a one-line fix, needs live debugging, or needs design judgment. First pilot data: the 2-file manifest task saved ~2% (fixed ~77K cached overhead dominates), the single-file logic task saved ~47%. Revise as data arrives.

Update 2026-11 (13 runs on the Istio and TinyOlly work, see IMPROVEMENTS.md): delegate only when ALL hold: a new file or one small function (at most 2 files named); a failing test or exact command written first defines done; requirements fully stated. Edits to existing logic across modules or fuzzy specs failed 6 of 6. Cap at 2 rounds (round 3 never rescued a task and the failures were the most expensive). If a task fails the gate, split it or do it directly. Best savings are mechanical: boilerplate, docs tables, extra test cases, condensing long logs.

## Quality bar (every delegated change)
1. The task's check passes and the regression command passes (independent re-run).
2. Tests and check scripts untouched; only files in "May change" changed.
3. Claude reads the diff and grades it: 2 merge as is, 1 merge after small fixes, 0 rewrite. A grade 0 counts as a failure of the method for that task kind.
