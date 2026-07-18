## 1. Contracts and shared types

- [x] 1.1 Define internal `stop_reason` enum and Chinese `stopReasonLabel` mapping (complete / cancelled / budget_* / breakers / max_tool_turns)
- [x] 1.2 Extend run completion StreamEvent (or adjacent schema) with optional camelCase `stopReason` + `stopReasonLabel`; keep backward compatible
- [x] 1.3 Mirror types on frontend shared definitions if required by existing event typing patterns
- [x] 1.4 Update `specs/05-adapter-interface.md` (and any docs citing `MAX_TURNS=8` as normal contract) to point at model-done + optional fuse

## 2. Remove default step cap (Custom only)

- [x] 2.1 Remove or neutralize product-default `MAX_TURNS = 8` in `custom_adapter.py` and `REACT_LOOP_MAX_TURNS = 8` in `agent_runner.py` for Custom/SDK path
- [x] 2.2 Ensure CLI adapters are untouched and still do not depend on the old Custom cap
- [x] 2.3 Add optional explicit `max_tool_turns` configuration (default unset/None); document that it is a fuse, not the product default

## 3. Unified termination state machine

- [x] 3.1 Consolidate existing mid-run compact (~90%) and hard token stop (~95%) into one pre-model-call decision function (single state machine, no dual branches)
- [x] 3.2 Implement soft wrap-up at ~92–93%: inject model-visible, user-hidden instruction; keep full tools; at most once per run
- [x] 3.3 Implement forced final: exactly one `tools=[]` (or ignore tool calls) call with user-facing natural-language summary template + short harness fact digest
- [x] 3.4 Guarantee soft/forced opportunity even if usage already crossed hard threshold before wrap-up ran
- [x] 3.5 Wire `max_tool_turns` hits into the same soft → forced pipeline and labels
- [x] 3.6 Propagate `stop_reason` through run result and completion events (no silent early return without reason)

## 4. Circuit breakers

- [x] 4.1 Implement stable tool fingerprint helper (sorted keys, path normalize; exclude volatile fields when practical; prefer under-trigger)
- [x] 4.2 Duplicate fingerprint consecutive-3 inject, then forced on continued identical call
- [x] 4.3 Same-tool consecutive execution error ≥3 inject, then forced on continued failure
- [x] 4.4 Compact consecutive failure ≥3: stop compact retries; enter soft → forced pipeline
- [x] 4.5 Ensure breakers only apply on Custom/SDK path

## 5. Nested run signaling

- [x] 5.1 When Custom child run stop_reason ≠ complete, prefix/annotate tool result returned to parent with abnormal-stop marker + final text
- [x] 5.2 Confirm no changes required to `task_dispatch` / `dispatch_plan` APIs beyond result string content

## 6. Frontend light hints

- [x] 6.1 Apply `stopReason` / `stopReasonLabel` from SSE/run end into conversation/run store
- [x] 6.2 Render lightweight Chinese hint for abnormal stops near run/message UI
- [x] 6.3 Ensure soft wrap-up inject never renders as a chat bubble
- [x] 6.4 Natural `complete` runs show no error-style stop banner

## 7. Eval, tests, and verification

- [x] 7.1 Update `eval_rules` (and related metrics) that hardcode `MAX_TURNS = 8` / `max_turns_exceeded` for the new semantics
- [x] 7.2 Unit/integration tests: model-done beyond 8 tools; soft then complete; soft then forced; hard path; each breaker; max_tool_turns fuse; event fields present
- [x] 7.3 Regression: CLI path tests still pass; orchestration dispatch tests unchanged in behavior
- [x] 7.4 Run backend `ruff check` + relevant `pytest`; frontend `pnpm typecheck` / lint if UI touched

## 8. Optional hardening

- [ ] 8.1 (Optional) Feature flag for rapid rollback to legacy cap — only if team wants belt-and-suspenders
- [ ] 8.2 (Optional) Make threshold percentages settings-configurable — default keep code constants for v1
