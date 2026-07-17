# Code Intelligence Real Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep both source-intelligence switches inside their tracks and replace the fake animated bar with a single monotonic whole-run percentage driven by live CodeGraph output.

**Architecture:** Run CodeGraph index commands with plain verbose progress, stream stdout while the child is alive, parse phase/current/total records, and combine real phase progress into a bounded monotonic `progressPercent`. Persist it in Workspace metadata and expose it through the existing REST polling contract. Reuse one client switch component in both UI locations and render the progress bar from the returned percentage; `ready` continues to use the existing success panel.

**Tech Stack:** Python 3.12, asyncio/subprocess, FastAPI/Pydantic, Next.js 16.2.6, React 19, TypeScript, Tailwind CSS, pytest, Vitest.

---

### Task 1: Specify real progress and acceptance fixes in OpenSpec

**Files:**
- Modify: `openspec/changes/add-code-intelligence-tool/specs/code-intelligence/spec.md`
- Modify: `openspec/changes/add-code-intelligence-tool/specs/frontend/spec.md`
- Modify: `openspec/changes/add-code-intelligence-tool/tasks.md`

- [ ] **Step 1: Add acceptance requirements**

Specify that active index/rebuild work reports a monotonic whole-run percentage derived only from live CodeGraph progress, and that completed work hides the bar in favor of the ready panel.

- [ ] **Step 2: Add ordered acceptance tasks**

Append task 7.1 for backend progress, 7.2 for switch/progress UI, and 7.3 for regression verification. Mark each task immediately after its verification passes.

### Task 2: Stream and persist CodeGraph progress

**Files:**
- Create: `backend/app/code_intelligence/progress.py`
- Modify: `backend/app/code_intelligence/metadata.py`
- Modify: `backend/app/code_intelligence/process_runner.py`
- Modify: `backend/app/code_intelligence/index_manager.py`
- Modify: `backend/app/code_intelligence/service.py`
- Test: `backend/tests/test_code_intelligence_progress.py`
- Test: `backend/tests/test_codegraph_process_runner.py`
- Test: `backend/tests/test_code_intelligence_metadata.py`

- [ ] **Step 1: Write failing parser and monotonic aggregation tests**

Test plain verbose records such as `[0.3s] Phase: parsing` and `[0.4s] 34/100 (34%)`, then assert the aggregator maps known phase completion into a monotonic `0..99` whole-run percentage and ignores malformed lines.

- [ ] **Step 2: Run the focused tests and confirm RED**

Run `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_code_intelligence_progress.py -q` and confirm failure because `progress.py` and `progressPercent` do not exist.

- [ ] **Step 3: Implement the progress model**

Add a focused parser/tracker module with a value object shaped as:

```python
@dataclass(frozen=True)
class CodeGraphProgress:
    phase: str
    phase_percent: int
    overall_percent: int
```

Use ordered CodeGraph phases (`scanning`, `parsing`, `storing`, `resolving`) and clamp active overall progress to `0..99`. Add nullable `progress_percent` / `progressPercent` to metadata.

- [ ] **Step 4: Write failing live-output callback tests**

Run a short Python child that prints two flushed progress lines with a delay. Assert the callback receives the first line before process completion on Windows and POSIX paths.

- [ ] **Step 5: Run the callback test and confirm RED**

Run the single new process-runner test and confirm the callback argument is unsupported or fires only after completion.

- [ ] **Step 6: Implement streaming subprocess capture**

Add an optional stdout-line callback to `run_process`. Drain stdout/stderr without deadlock, retain bounded final capture, preserve cancellation/timeout/process-tree cleanup, and add `--verbose` to init/rebuild argv so progress is newline-delimited and parseable.

- [ ] **Step 7: Wire progress through manager and service**

Pass a synchronous progress callback with each managed index operation. Update Workspace metadata only when parsed progress increases; clear the active percentage on ready/failed/interrupted/disabled while preserving existing counts and state transitions.

- [ ] **Step 8: Run backend tests and mark OpenSpec task 7.1 complete**

Run all `test_code_intelligence_*.py`, `test_codegraph_process_runner.py`, and Ruff for modified backend files. Expected: all pass with no lint findings.

### Task 3: Render a bounded shared switch and real percentage

**Files:**
- Create: `src/components/code-intelligence-switch.tsx`
- Modify: `src/components/new-conversation-dialog.tsx`
- Modify: `src/components/code-intelligence-control.tsx`
- Modify: `src/lib/api.ts`
- Modify: `src/lib/code-intelligence.ts`
- Test: `src/lib/code-intelligence.test.ts`
- Test: `src/components/code-intelligence-switch.test.tsx`

- [ ] **Step 1: Write failing frontend progress tests**

Extend the status record with `progressPercent`. Assert active progress returns the exact server percentage and ready progress is inactive with no percentage.

- [ ] **Step 2: Write failing shared-switch boundary test**

Render the shared switch OFF and ON, asserting the thumb always has an explicit left inset and the ON transform remains within the 36px track.

- [ ] **Step 3: Run focused Vitest tests and confirm RED**

Run `pnpm vitest run src/lib/code-intelligence.test.ts src/components/code-intelligence-switch.test.tsx` and confirm failures for the missing percentage and shared component.

- [ ] **Step 4: Implement the shared switch**

Create a controlled client component using a `relative h-5 w-9` track and `absolute left-0.5 top-0.5 size-4` thumb; OFF uses no translation and ON uses `translate-x-4`. Replace both handwritten switches with this component.

- [ ] **Step 5: Implement the A-layout progress region**

Render one bordered progress section with label and `{progressPercent}%`; set fill width from the bounded value. Do not use `animate-pulse`. When status is ready, render only the existing green ready summary/details/actions.

- [ ] **Step 6: Run frontend tests/typecheck and mark OpenSpec task 7.2 complete**

Run focused Vitest, the complete frontend test suite, and `pnpm typecheck`. Expected: all pass and no TypeScript errors.

### Task 4: End-to-end verification

**Files:**
- Modify: `openspec/changes/add-code-intelligence-tool/tasks.md`
- Modify: `openspec/changes/add-code-intelligence-tool/verification.md`

- [ ] **Step 1: Verify live indexing**

Rebuild a representative project and poll the REST status. Record that `progressPercent` changes while indexing, never decreases, and the final status is `ready` with the expected counts.

- [ ] **Step 2: Verify visual states**

Check create-dialog OFF/ON, panel OFF/ON, active progress, and ready state. Confirm both thumbs remain inside their tracks and ready matches the approved screenshot.

- [ ] **Step 3: Run final regression**

Run backend source-intelligence tests, Ruff, frontend tests, typecheck, and `git diff --check`. Do not modify or restore existing `package.json` or `pnpm-lock.yaml` changes.

- [ ] **Step 4: Record evidence and mark OpenSpec task 7.3 complete**

Append exact test counts and live progress evidence to `verification.md`, then update the final task checkbox. Do not commit.
