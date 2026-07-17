# Code Intelligence Status UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify the source-intelligence status panel and replace the persistent corner notice with an obvious, auto-dismissing top-center notification.

**Architecture:** Keep status derivation as pure functions in `src/lib/code-intelligence.ts` so it is testable without a browser DOM. Split transition detection from notification lifetime: status changes create a notice, while a separate timer owns dismissal and is unaffected by polling objects with the same status.

**Tech Stack:** React 19, TypeScript, Tailwind CSS, Vitest fake timers, Lucide icons.

---

### Task 1: Panel view model

**Files:**
- Modify: `src/lib/code-intelligence.ts`
- Test: `src/lib/code-intelligence.test.ts`

- [x] **Step 1: Write failing tests for useful status details**

Add tests asserting that disabled, working, ready and failed states receive explicit summary labels, and that detail rows omit null phase/error, unsynced timestamps and all-zero counts.

- [x] **Step 2: Run the focused test and verify RED**

Run: `node node_modules/vitest/vitest.mjs run src/lib/code-intelligence.test.ts`

Expected: failure because `getCodeIntelligencePanelSummary` and filtered detail behavior do not exist.

- [x] **Step 3: Implement the minimal pure view model**

Add `getCodeIntelligencePanelSummary(status)` returning a Chinese label and tone. Update `buildCodeIntelligenceDetailRows` to return project always, runtime only when known, statistics only when non-zero, and last sync only when present.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the Step 2 command. Expected: all focused tests pass.

### Task 2: Stable transient notification

**Files:**
- Modify: `src/lib/code-intelligence.ts`
- Modify: `src/components/code-intelligence-control.tsx`
- Test: `src/lib/code-intelligence.test.ts`

- [x] **Step 1: Write failing timer tests**

Using Vitest fake timers, assert `scheduleCodeIntelligenceNoticeDismiss` fires once after 4000 ms, can be cancelled, and a replacement timer starts a fresh lifetime.

- [x] **Step 2: Run focused tests and verify RED**

Run: `node node_modules/vitest/vitest.mjs run src/lib/code-intelligence.test.ts`

Expected: failure because the scheduler is missing.

- [x] **Step 3: Implement timer ownership and notification UI**

Add the scheduler helper. In the component, make transition detection depend on `status?.status`, use a separate effect keyed by `notice`, and render the notification at `fixed left-1/2 top-4` with success/error background, icon, close button, `aria-live`, and a short exit animation class.

- [x] **Step 4: Simplify the panel presentation**

Render a colored status summary below the header, show progress only while active, show a red error card only when an error exists, and render only the filtered detail rows. Keep existing legal action buttons and pending switch behavior.

- [x] **Step 5: Run tests, lint and type checks**

Run:

```text
node node_modules/vitest/vitest.mjs run src/lib/code-intelligence.test.ts
node node_modules/eslint/bin/eslint.js src/components/code-intelligence-control.tsx src/lib/code-intelligence.ts src/lib/code-intelligence.test.ts
node node_modules/typescript/bin/tsc --noEmit
```

Expected: focused tests and changed-file ESLint pass; TypeScript reports no errors in changed files (repository baseline errors may remain elsewhere).

No commit is created, per user instruction.
