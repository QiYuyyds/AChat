## Verification record

Date: 2026-07-17

### Representative effectiveness comparison

The benchmark fixture contained the 13 implementation files for source intelligence, the REST/tool entry points, the MCP bridge and agent runner. CodeGraph 0.9.3 indexed it in 239 ms and produced 357 nodes / 344 edges.

| Task | Without code intelligence | With `code_explore` equivalent | Tool-call reduction | Context reduction |
|---|---:|---:|---:|---:|
| Enable endpoint → process call chain | 4 calls / 26,676 chars | 1 call / 7,725 chars | 75.0% | 71.0% |
| `enabled` semantic impact | 4 calls / 17,292 chars | 1 call / 6,324 chars | 75.0% | 63.4% |
| Architecture summary | 8 calls / 47,302 chars | 1 call / 6,591 chars | 87.5% | 86.1% |

The baseline used actual `rg` plus targeted file reads. The CodeGraph side used one `context` query with the same bounded settings as `code_explore` (`--max-nodes 50 --max-code 10 --format markdown`).

### Regressions observed

- The call-chain query returned the API and service layers but omitted `process_runner.py`.
- The symbol-impact query returned metadata and service context but omitted `state_machine.py`.
- The broad architecture query over-prioritized `index_manager.py` and did not cover all expected runtime, service, tool and API modules.
- Code intelligence therefore reduces calls and context substantially, but it is supplemental context rather than a complete replacement for targeted search/read fallback.

### Quality gates

- Code Intelligence backend tests: 50 passed.
- Frontend tests: 77 passed, including 27 source-intelligence tests.
- Targeted Ruff and ESLint for changed/new source-intelligence files: passed.
- Electron TypeScript and CodeGraph packaged-runtime SHA smoke: passed.
- Repository-wide baseline remains red outside this change: Ruff reports 164 existing issues; TypeScript reports 8 existing errors; full backend pytest reports 201 failed / 91 errors, led by legacy fixtures missing required `user_id` and unrelated authentication/API expectations.

### Acceptance fixes: bounded switches and real progress

- Real CodeGraph rebuild against `D:\codegraph\codegraph` emitted observed whole-run progress `[0, 26, 27, 28, 30, 31, 32, 34, 35, 36, 37, 38, 40, 41, 42, 44, 45, 46, 47, 48, 50, 80, 86, 92, 99]`.
- The observed sequence was monotonic. Completion changed metadata to `ready`, cleared `progressPercent`, and retained `348 files / 5500 symbols / 14958 relationships`.
- Code Intelligence backend regression: 45 passed; targeted Ruff passed.
- Frontend regression: 88 passed; targeted ESLint passed.
- `git diff --check` passed (line-ending conversion warnings only).
- Repository-wide TypeScript remains blocked by the same 8 unrelated baseline errors in `message-input.tsx`, `particle-background.tsx`, `profile-dialog.tsx`, `app-store.ts` and `app-store.test.ts`; none is in a source-intelligence file.
- Browser interaction verification stopped at the existing login screen; no credentials were read or entered. Switch boundary behavior is covered by the shared switch implementation and focused frontend test.
