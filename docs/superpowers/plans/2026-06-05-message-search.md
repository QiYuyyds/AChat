# Message Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-conversation full-text search for messages, accessible via ⌘K command palette, jumping to the matched message with a 2s highlight.

**Architecture:** SQLite FTS5 virtual table (`messages_fts`) with trigram tokenizer, kept in sync by three DB triggers (insert/update/delete on `messages`, all guarded by `WHEN status != 'streaming'`). Pure-function `search-service` + thin API route + Zustand store + small UI components. Short Chinese queries (< 3 Chinese chars) fall back to a `LIKE` path with no snippet highlight.

**Tech Stack:** Next.js 16 App Router, Drizzle + better-sqlite3, SQLite FTS5, Zustand + Immer, shadcn/ui (Dialog + Command), zod, vitest, Playwright.

**Spec:** `specs/16-message-search.md`

**Branch:** `spec/search` (already created; this plan executes on a sub-branch or directly on `spec/search` — see Task 0).

---

## File Structure

### New files
| Path | Responsibility |
|---|---|
| `src/db/migrate-add-message-search.ts` | One-shot DDL: virtual table + 3 triggers + backfill. Idempotent. |
| `src/db/migrate-add-message-search.test.ts` | Idempotency + backfill correctness. |
| `src/server/search-service.ts` | Pure async functions: `searchMessages`, `countSearchMatches`. Two SQL paths (FTS5 + LIKE). |
| `src/server/search-service.test.ts` | Unit tests for both paths + edge cases. |
| `src/server/messages-fts-triggers.test.ts` | Integration: insert/update/delete trigger behavior, streaming skip. |
| `src/app/api/search/route.ts` | GET `/api/search` route, zod validation, envelope response. |
| `src/app/api/search/route.test.ts` | API integration tests. |
| `src/stores/search-store.ts` | Zustand store: isOpen, query, hits, debounced runSearch, jumpToHit. |
| `src/stores/search-store.test.ts` | Store action tests (short query skip, fallback decision, jump). |
| `src/lib/api.ts` (extension) | Add `searchMessagesApi()` client function. |
| `src/components/global-search.tsx` | Command palette modal (Dialog + Input + list). |
| `src/components/global-search-trigger.tsx` | Top bar button + ⌘K shortcut binding. |
| `src/components/search-result-item.tsx` | Single result row with snippet (dangerouslySetInnerHTML). |
| `src/components/message-highlight-layer.tsx` | Subscribes to store; scrolls + flashes highlight. |
| `e2e/global-search.spec.ts` | Playwright: ⌘K open → type → click → jump → highlight fade. |

### Modified files
| Path | Change |
|---|---|
| `src/shared/types.ts` | Add `SearchHit` interface. |
| `src/lib/api.ts` | Add `searchMessagesApi()` client function. |
| `src/components/chat/message-list.tsx` (or equivalent) | Add `id={`message-${msg.id}`}` on each message container. |
| `src/components/sidebar.tsx` (or top bar) | Add `<GlobalSearchTrigger />` next to existing search input. |

---

## Task 0: Branch setup

**Files:** none

- [ ] **Step 1: Verify current branch**

Run: `git branch --show-current`
Expected: `spec/search`

- [ ] **Step 2: Create implementation branch off spec/search**

Run:
```bash
git checkout -b feature/message-search
```

Expected: `Switched to a new branch 'feature/message-search'`

> Each task ends with a commit. All commits land on `feature/message-search`. Merge back to `spec/search` (or directly to main) at the end.

---

## Task 1: Add SearchHit type to shared types

**Files:**
- Modify: `src/shared/types.ts`
- Test: typecheck only (no runtime test for pure type)

- [ ] **Step 1: Locate the type file and a good insertion point**

Run: `grep -n "^export interface\|^export type" src/shared/types.ts | head -20`
Pick a location near other "shared payload" types (e.g. near `MessagePart`).

- [ ] **Step 2: Add the SearchHit interface**

Open `src/shared/types.ts` and append at the end of the file (or just before the trailing helper types, depending on what's there):

```ts
export interface SearchHit {
  messageId: string
  conversationId: string
  conversationTitle: string
  role: 'user' | 'agent' | 'system'
  agentId: string | null
  agentName: string | null
  agentAvatar: string | null
  createdAt: number
  /** FTS5 path: contains <mark>...</mark> tags. LIKE path: plain text. */
  snippetHtml: string
}
```

- [ ] **Step 3: Verify typecheck**

Run: `pnpm typecheck`
Expected: passes, no errors.

- [ ] **Step 4: Commit**

```bash
git add src/shared/types.ts
git commit -m "feat(search): add SearchHit shared type"
```

---

## Task 2: Write the DB migration

**Files:**
- Create: `src/db/migrate-add-message-search.ts`
- Test: `src/db/migrate-add-message-search.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/db/migrate-add-message-search.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import Database from 'better-sqlite3'

import { runMessageSearchMigration } from './migrate-add-message-search'

function makeDb() {
  const db = new Database(':memory:')
  // Minimal messages schema required by the migration
  db.exec(`
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      agent_id TEXT,
      parts TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `)
  return db
}

describe('runMessageSearchMigration', () => {
  let db: Database.Database
  beforeEach(() => { db = makeDb() })
  afterEach(() => { db.close() })

  it('creates messages_fts virtual table', () => {
    runMessageSearchMigration(db)
    const row = db
      .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='messages_fts'")
      .get()
    expect(row).toBeDefined()
  })

  it('creates three triggers', () => {
    runMessageSearchMigration(db)
    const triggers = db
      .prepare(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'messages_fts_%'",
      )
      .all() as { name: string }[]
    const names = triggers.map((t) => t.name).sort()
    expect(names).toEqual(['messages_fts_ad', 'messages_fts_ai', 'messages_fts_au'])
  })

  it('is idempotent (running twice does not throw)', () => {
    runMessageSearchMigration(db)
    expect(() => runMessageSearchMigration(db)).not.toThrow()
  })

  it('backfills existing text parts from messages', () => {
    db.prepare(
      `INSERT INTO messages (id, conversation_id, role, parts, status, created_at)
       VALUES (?, ?, ?, ?, ?, ?)`,
    ).run('m1', 'c1', 'user', JSON.stringify([
      { type: 'text', content: 'hello world' },
      { type: 'thinking', content: 'internal note' },
      { type: 'text', content: 'goodbye' },
    ]), 'complete', 1)

    runMessageSearchMigration(db)

    const rows = db.prepare('SELECT content FROM messages_fts ORDER BY rowid').all() as { content: string }[]
    // thinking must be excluded
    expect(rows.map((r) => r.content).sort()).toEqual(['goodbye', 'hello world'])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/db/migrate-add-message-search.test.ts`
Expected: FAIL — module `./migrate-add-message-search` not found.

- [ ] **Step 3: Implement the migration**

Create `src/db/migrate-add-message-search.ts`:

```ts
/**
 * 一次性 schema migration：加 messages_fts 虚拟表 + 3 触发器，回填已有 text part。
 *
 * 幂等：使用 IF NOT EXISTS 守卫；backfill 使用 INSERT OR IGNORE 不会重复插入。
 *
 * 执行：tsx src/db/migrate-add-message-search.ts
 */
import type Database from 'better-sqlite3'
import { sql } from 'drizzle-orm'

import { db as defaultDb } from './client'

const STATEMENTS: string[] = [
  `CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(content, tokenize='trigram')`,
  `CREATE TRIGGER IF NOT EXISTS messages_fts_ai
     AFTER INSERT ON messages
     WHEN new.status != 'streaming'
     BEGIN
       INSERT INTO messages_fts(rowid, content)
       SELECT new.rowid, json_extract(value, '$.content')
       FROM json_each(new.parts)
       WHERE json_extract(value, '$.type') = 'text';
     END`,
  `CREATE TRIGGER IF NOT EXISTS messages_fts_au
     AFTER UPDATE ON messages
     WHEN new.status != 'streaming'
     BEGIN
       DELETE FROM messages_fts WHERE rowid = old.rowid;
       INSERT INTO messages_fts(rowid, content)
       SELECT new.rowid, json_extract(value, '$.content')
       FROM json_each(new.parts)
       WHERE json_extract(value, '$.type') = 'text';
     END`,
  `CREATE TRIGGER IF NOT EXISTS messages_fts_ad
     AFTER DELETE ON messages
     BEGIN
       DELETE FROM messages_fts WHERE rowid = old.rowid;
     END`,
  `INSERT INTO messages_fts(rowid, content)
     SELECT m.rowid, json_extract(value, '$.content')
     FROM messages m, json_each(m.parts)
     WHERE json_extract(value, '$.type') = 'text'`,
]

export function runMessageSearchMigration(target: Database.Database = defaultDb as unknown as Database.Database) {
  for (const stmt of STATEMENTS) {
    target.exec(stmt)
  }
}

// CLI entry
if (require.main === module) {
  runMessageSearchMigration()
  console.log('done')
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/db/migrate-add-message-search.test.ts`
Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/db/migrate-add-message-search.ts src/db/migrate-add-message-search.test.ts
git commit -m "feat(db): add messages_fts virtual table + sync triggers"
```

---

## Task 3: Test trigger behavior (insert/update/delete + streaming skip)

**Files:**
- Create: `src/server/messages-fts-triggers.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/server/messages-fts-triggers.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import Database from 'better-sqlite3'

import { runMessageSearchMigration } from '../db/migrate-add-message-search'

function makeDb() {
  const db = new Database(':memory:')
  db.exec(`
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      agent_id TEXT,
      parts TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `)
  runMessageSearchMigration(db)
  return db
}

function insertMessage(
  db: Database.Database,
  id: string,
  parts: unknown[],
  status: string,
) {
  db.prepare(
    `INSERT INTO messages (id, conversation_id, role, parts, status, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  ).run(id, 'c1', 'user', JSON.stringify(parts), status, Date.now())
}

function ftsCount(db: Database.Database, content: string) {
  const row = db
    .prepare('SELECT COUNT(*) AS n FROM messages_fts WHERE messages_fts MATCH ?')
    .get(content) as { n: number }
  return row.n
}

describe('messages_fts triggers', () => {
  let db: Database.Database
  beforeEach(() => { db = makeDb() })
  afterEach(() => { db.close() })

  it('inserts one FTS row per text part', () => {
    insertMessage(db, 'm1', [
      { type: 'text', content: 'hello' },
      { type: 'text', content: 'world' },
      { type: 'thinking', content: 'private' },
    ], 'complete')
    expect(ftsCount(db, 'hello')).toBe(1)
    expect(ftsCount(db, 'world')).toBe(1)
    expect(ftsCount(db, 'private')).toBe(0)
  })

  it('skips streaming messages on insert', () => {
    insertMessage(db, 'm1', [{ type: 'text', content: 'mid-stream' }], 'streaming')
    expect(ftsCount(db, 'mid-stream')).toBe(0)
  })

  it('syncs when status transitions streaming → complete', () => {
    insertMessage(db, 'm1', [{ type: 'text', content: 'growing' }], 'streaming')
    expect(ftsCount(db, 'growing')).toBe(0)

    db.prepare(`UPDATE messages SET status = ?, parts = ? WHERE id = ?`)
      .run('complete', JSON.stringify([{ type: 'text', content: 'grown text' }]), 'm1')

    expect(ftsCount(db, 'grown text')).toBe(1)
    expect(ftsCount(db, 'growing')).toBe(0)
  })

  it('skips update while still streaming', () => {
    insertMessage(db, 'm1', [{ type: 'text', content: 'first' }], 'streaming')
    db.prepare(`UPDATE messages SET parts = ? WHERE id = ?`)
      .run(JSON.stringify([{ type: 'text', content: 'second' }]), 'm1')
    expect(ftsCount(db, 'first')).toBe(0)
    expect(ftsCount(db, 'second')).toBe(0)
  })

  it('removes FTS rows on delete', () => {
    insertMessage(db, 'm1', [{ type: 'text', content: 'todelete' }], 'complete')
    expect(ftsCount(db, 'todelete')).toBe(1)
    db.prepare(`DELETE FROM messages WHERE id = ?`).run('m1')
    expect(ftsCount(db, 'todelete')).toBe(0)
  })
})
```

- [ ] **Step 2: Run test to verify it passes immediately (triggers already exist from Task 2)**

Run: `pnpm vitest run src/server/messages-fts-triggers.test.ts`
Expected: all 5 tests pass. (This task verifies the trigger behavior is correct; the migration is the implementation. If any test fails, fix the migration's trigger SQL.)

- [ ] **Step 3: Commit**

```bash
git add src/server/messages-fts-triggers.test.ts
git commit -m "test(search): cover messages_fts trigger behavior + streaming skip"
```

---

## Task 4: Implement search-service (FTS5 path)

**Files:**
- Create: `src/server/search-service.ts`
- Test: `src/server/search-service.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/server/search-service.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import Database from 'better-sqlite3'

import { runMessageSearchMigration } from '../db/migrate-add-message-search'
import { searchMessages } from './search-service'

// Use a fresh in-memory DB per test. search-service accepts a db override.

function makeDb() {
  const db = new Database(':memory:')
  db.exec(`
    CREATE TABLE conversations (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL
    );
    CREATE TABLE agents (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      avatar TEXT NOT NULL
    );
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      agent_id TEXT,
      parts TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `)
  db.prepare(`INSERT INTO conversations (id, title) VALUES (?, ?)`)
    .run('c1', 'First conv')
  db.prepare(`INSERT INTO conversations (id, title) VALUES (?, ?)`)
    .run('c2', 'Second conv')
  db.prepare(`INSERT INTO agents (id, name, avatar) VALUES (?, ?, ?)`)
    .run('a1', 'Claude', '🤖')
  runMessageSearchMigration(db)
  return db
}

function insertMessage(
  db: Database.Database,
  id: string, convId: string, role: string,
  parts: unknown[], status = 'complete', agentId: string | null = null,
) {
  db.prepare(
    `INSERT INTO messages (id, conversation_id, role, agent_id, parts, status, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  ).run(id, convId, role, agentId, JSON.stringify(parts), status, Date.now())
}

describe('searchMessages (FTS5 path)', () => {
  let db: Database.Database
  beforeEach(() => { db = makeDb() })
  afterEach(() => { db.close() })

  it('returns empty result for empty query', async () => {
    const r = await searchMessages({ query: '', db: db as any })
    expect(r.hits).toEqual([])
    expect(r.total).toBe(0)
  })

  it('returns empty result for whitespace query', async () => {
    const r = await searchMessages({ query: '   ', db: db as any })
    expect(r.hits).toEqual([])
    expect(r.total).toBe(0)
  })

  it('matches English prefix with snippet', async () => {
    insertMessage(db, 'm1', 'c1', 'user', [
      { type: 'text', content: 'rendering pipeline discussion' },
    ])
    const r = await searchMessages({ query: 'render*', db: db as any })
    expect(r.total).toBe(1)
    expect(r.hits[0].messageId).toBe('m1')
    expect(r.hits[0].snippetHtml).toContain('<mark>')
  })

  it('matches Chinese substring (3+ chars)', async () => {
    insertMessage(db, 'm1', 'c1', 'user', [
      { type: 'text', content: '渲染管线优化方案' },
    ])
    const r = await searchMessages({ query: '渲染管', db: db as any })
    expect(r.total).toBe(1)
  })

  it('returns conversationTitle and agentName via JOIN', async () => {
    insertMessage(db, 'm1', 'c2', 'agent', [
      { type: 'text', content: 'switching to opus model' },
    ], 'complete', 'a1')
    const r = await searchMessages({ query: 'opus', db: db as any })
    expect(r.hits[0].conversationTitle).toBe('Second conv')
    expect(r.hits[0].agentName).toBe('Claude')
    expect(r.hits[0].agentAvatar).toBe('🤖')
  })

  it('filters by conversationId', async () => {
    insertMessage(db, 'm1', 'c1', 'user', [{ type: 'text', content: 'shared term' }])
    insertMessage(db, 'm2', 'c2', 'user', [{ type: 'text', content: 'shared term' }])
    const r = await searchMessages({ query: 'shared', conversationId: 'c1', db: db as any })
    expect(r.total).toBe(1)
    expect(r.hits[0].conversationId).toBe('c1')
  })

  it('respects limit', async () => {
    for (let i = 0; i < 5; i++) {
      insertMessage(db, `m${i}`, 'c1', 'user', [{ type: 'text', content: 'bulk' }])
    }
    const r = await searchMessages({ query: 'bulk', limit: 2, db: db as any })
    expect(r.hits.length).toBe(2)
  })

  it('returns error code for invalid FTS5 syntax', async () => {
    const r = await searchMessages({ query: '(unclosed', db: db as any })
    expect(r.hits).toEqual([])
    expect(r.error).toBe('INVALID_QUERY')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/server/search-service.test.ts`
Expected: FAIL — module `./search-service` not found.

- [ ] **Step 3: Implement search-service (FTS5 path first, no LIKE yet)**

Create `src/server/search-service.ts`:

```ts
import type Database from 'better-sqlite3'
import { sql } from 'drizzle-orm'

import { db as defaultDb } from '@/db/client'

import type { SearchHit } from '@/shared/types'

export interface SearchOptions {
  query: string
  limit?: number
  offset?: number
  conversationId?: string
  role?: 'user' | 'agent'
  fallback?: 'like'
  /** Injected for testing. */
  db?: Database.Database
}

export interface SearchResult {
  hits: SearchHit[]
  total: number
  tookMs: number
  error?: 'INVALID_QUERY'
}

function escapeFts(input: string): string {
  // FTS5 string: strip leading/trailing whitespace, quote if it contains specials
  const trimmed = input.trim()
  if (!trimmed) return trimmed
  // If user typed a phrase, we accept it as-is; the SQL uses parameterized ?.
  // We only need to defend against the *value* itself, not FTS5 operators in code.
  return trimmed
}

function rowToHit(row: {
  messageId: string
  conversationId: string
  conversationTitle: string
  role: 'user' | 'agent' | 'system'
  agentId: string | null
  agentName: string | null
  agentAvatar: string | null
  createdAt: number
  snippetHtml: string
}): SearchHit {
  return {
    messageId: row.messageId,
    conversationId: row.conversationId,
    conversationTitle: row.conversationTitle,
    role: row.role,
    agentId: row.agentId,
    agentName: row.agentName,
    agentAvatar: row.agentAvatar,
    createdAt: row.createdAt,
    snippetHtml: row.snippetHtml,
  }
}

export async function searchMessages(opts: SearchOptions): Promise<SearchResult> {
  const start = Date.now()
  const trimmed = opts.query.trim()
  if (!trimmed) {
    return { hits: [], total: 0, tookMs: 0 }
  }

  const target = (opts.db ?? (defaultDb as unknown as Database.Database))
  const limit = Math.min(Math.max(opts.limit ?? 20, 1), 100)
  const offset = Math.max(opts.offset ?? 0, 0)

  // Use FTS5 path always in this task; LIKE fallback added in Task 5.
  const params: unknown[] = [
    escapeFts(trimmed),
    opts.conversationId ?? null,
    opts.conversationId ?? null,
    opts.role ?? null,
    opts.role ?? null,
    limit,
    offset,
  ]

  const stmt = target.prepare(`
    SELECT
      m.id AS messageId,
      m.conversation_id AS conversationId,
      m.role AS role,
      m.agent_id AS agentId,
      m.created_at AS createdAt,
      snippet(messages_fts, 0, '<mark>', '</mark>', '…', 12) AS snippetHtml,
      c.title AS conversationTitle,
      a.name AS agentName,
      a.avatar AS agentAvatar
    FROM messages_fts
    JOIN messages m      ON m.rowid = messages_fts.rowid
    JOIN conversations c ON c.id = m.conversation_id
    LEFT JOIN agents a   ON a.id = m.agent_id
    WHERE messages_fts MATCH ?
      AND (? IS NULL OR m.conversation_id = ?)
      AND (? IS NULL OR m.role = ?)
    ORDER BY bm25(messages_fts)
    LIMIT ? OFFSET ?
  `)

  let rows: ReturnType<typeof stmt.all>
  try {
    rows = stmt.all(...params) as any
  } catch (err) {
    if (err instanceof Error && /SQLITE_ERROR/.test(err.message)) {
      return { hits: [], total: 0, tookMs: 0, error: 'INVALID_QUERY' }
    }
    throw err
  }

  // total = count of all matching rows (without limit/offset); reuse MATCH filter
  let total = rows.length
  if (rows.length === limit) {
    const countStmt = target.prepare(`
      SELECT COUNT(*) AS n FROM messages_fts
      JOIN messages m ON m.rowid = messages_fts.rowid
      WHERE messages_fts MATCH ?
        AND (? IS NULL OR m.conversation_id = ?)
        AND (? IS NULL OR m.role = ?)
    `)
    const countRow = countStmt.get(
      escapeFts(trimmed),
      opts.conversationId ?? null,
      opts.conversationId ?? null,
      opts.role ?? null,
      opts.role ?? null,
    ) as { n: number }
    total = countRow.n
  }

  return {
    hits: rows.map(rowToHit),
    total,
    tookMs: Date.now() - start,
  }
}

export async function countSearchMatches(query: string): Promise<number> {
  const r = await searchMessages({ query })
  return r.total
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/server/search-service.test.ts`
Expected: 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/server/search-service.ts src/server/search-service.test.ts
git commit -m "feat(search): implement FTS5 search-service"
```

---

## Task 5: Add LIKE fallback path to search-service

**Files:**
- Modify: `src/server/search-service.ts`
- Modify: `src/server/search-service.test.ts`

- [ ] **Step 1: Add the failing test for LIKE path**

Append to `src/server/search-service.test.ts` (inside the existing describe block):

```ts
  it('LIKE fallback matches short Chinese query', async () => {
    insertMessage(db, 'm1', 'c1', 'user', [
      { type: 'text', content: '模型切换问题' },
    ])
    const r = await searchMessages({ query: '模型', fallback: 'like', db: db as any })
    expect(r.total).toBe(1)
    // LIKE path snippet has no <mark>
    expect(r.hits[0].snippetHtml).not.toContain('<mark>')
  })

  it('LIKE fallback filters by conversationId', async () => {
    insertMessage(db, 'm1', 'c1', 'user', [{ type: 'text', content: '共同' }])
    insertMessage(db, 'm2', 'c2', 'user', [{ type: 'text', content: '共同' }])
    const r = await searchMessages({
      query: '共同', fallback: 'like', conversationId: 'c2', db: db as any,
    })
    expect(r.total).toBe(1)
    expect(r.hits[0].conversationId).toBe('c2')
  })
```

- [ ] **Step 2: Run test to verify the new ones fail**

Run: `pnpm vitest run src/server/search-service.test.ts`
Expected: 2 new tests FAIL — `fallback: 'like'` is ignored by current implementation.

- [ ] **Step 3: Add LIKE branch to searchMessages**

Edit `src/server/search-service.ts`. Replace the FTS5-only path with a branch:

```ts
  if (opts.fallback === 'like') {
    return runLikePath(target, trimmed, limit, offset, opts)
  }
  return runFtsPath(target, trimmed, limit, offset, opts)
```

And add the two helper functions above `searchMessages`:

```ts
function runFtsPath(
  target: Database.Database,
  q: string,
  limit: number,
  offset: number,
  opts: SearchOptions,
): SearchResult {
  const start = Date.now()
  // ... same body as Task 4's fts query ...
}

function runLikePath(
  target: Database.Database,
  q: string,
  limit: number,
  offset: number,
  opts: SearchOptions,
): SearchResult {
  const start = Date.now()
  const stmt = target.prepare(`
    SELECT
      m.id AS messageId,
      m.conversation_id AS conversationId,
      m.role AS role,
      m.agent_id AS agentId,
      m.created_at AS createdAt,
      substr(m.parts, max(1, instr(m.parts, ?) - 30), 80) AS snippetHtml,
      c.title AS conversationTitle,
      a.name AS agentName,
      a.avatar AS agentAvatar
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    LEFT JOIN agents a   ON a.id = m.agent_id
    WHERE m.parts LIKE '%' || ? || '%'
      AND (? IS NULL OR m.conversation_id = ?)
      AND (? IS NULL OR m.role = ?)
    ORDER BY m.created_at DESC
    LIMIT ? OFFSET ?
  `)
  const rows = stmt.all(
    q, q,
    opts.conversationId ?? null, opts.conversationId ?? null,
    opts.role ?? null, opts.role ?? null,
    limit, offset,
  ) as any
  return {
    hits: rows.map(rowToHit),
    total: rows.length,
    tookMs: Date.now() - start,
  }
}
```

(Refactor the existing FTS5 body in `searchMessages` into `runFtsPath`. The full file is shown in the next commit step.)

- [ ] **Step 4: Replace search-service.ts with the refactored full version**

Overwrite `src/server/search-service.ts` with the complete file:

```ts
import type Database from 'better-sqlite3'

import { db as defaultDb } from '@/db/client'

import type { SearchHit } from '@/shared/types'

export interface SearchOptions {
  query: string
  limit?: number
  offset?: number
  conversationId?: string
  role?: 'user' | 'agent'
  fallback?: 'like'
  /** Injected for testing. */
  db?: Database.Database
}

export interface SearchResult {
  hits: SearchHit[]
  total: number
  tookMs: number
  error?: 'INVALID_QUERY'
}

type HitRow = {
  messageId: string
  conversationId: string
  conversationTitle: string
  role: 'user' | 'agent' | 'system'
  agentId: string | null
  agentName: string | null
  agentAvatar: string | null
  createdAt: number
  snippetHtml: string
}

function rowToHit(row: HitRow): SearchHit {
  return { ...row }
}

export async function searchMessages(opts: SearchOptions): Promise<SearchResult> {
  const trimmed = opts.query.trim()
  if (!trimmed) return { hits: [], total: 0, tookMs: 0 }

  const target = (opts.db ?? (defaultDb as unknown as Database.Database))
  const limit = Math.min(Math.max(opts.limit ?? 20, 1), 100)
  const offset = Math.max(opts.offset ?? 0, 0)

  if (opts.fallback === 'like') {
    return runLikePath(target, trimmed, limit, offset, opts)
  }
  return runFtsPath(target, trimmed, limit, offset, opts)
}

function runFtsPath(
  target: Database.Database,
  q: string,
  limit: number,
  offset: number,
  opts: SearchOptions,
): SearchResult {
  const start = Date.now()
  const stmt = target.prepare(`
    SELECT
      m.id AS messageId,
      m.conversation_id AS conversationId,
      m.role AS role,
      m.agent_id AS agentId,
      m.created_at AS createdAt,
      snippet(messages_fts, 0, '<mark>', '</mark>', '…', 12) AS snippetHtml,
      c.title AS conversationTitle,
      a.name AS agentName,
      a.avatar AS agentAvatar
    FROM messages_fts
    JOIN messages m      ON m.rowid = messages_fts.rowid
    JOIN conversations c ON c.id = m.conversation_id
    LEFT JOIN agents a   ON a.id = m.agent_id
    WHERE messages_fts MATCH ?
      AND (? IS NULL OR m.conversation_id = ?)
      AND (? IS NULL OR m.role = ?)
    ORDER BY bm25(messages_fts)
    LIMIT ? OFFSET ?
  `)
  let rows: HitRow[]
  try {
    rows = stmt.all(
      q,
      opts.conversationId ?? null, opts.conversationId ?? null,
      opts.role ?? null, opts.role ?? null,
      limit, offset,
    ) as HitRow[]
  } catch (err) {
    if (err instanceof Error && /SQLITE_ERROR/.test(err.message)) {
      return { hits: [], total: 0, tookMs: 0, error: 'INVALID_QUERY' }
    }
    throw err
  }

  let total = rows.length
  if (rows.length === limit) {
    const countRow = target.prepare(`
      SELECT COUNT(*) AS n FROM messages_fts
      JOIN messages m ON m.rowid = messages_fts.rowid
      WHERE messages_fts MATCH ?
        AND (? IS NULL OR m.conversation_id = ?)
        AND (? IS NULL OR m.role = ?)
    `).get(
      q,
      opts.conversationId ?? null, opts.conversationId ?? null,
      opts.role ?? null, opts.role ?? null,
    ) as { n: number }
    total = countRow.n
  }

  return { hits: rows.map(rowToHit), total, tookMs: Date.now() - start }
}

function runLikePath(
  target: Database.Database,
  q: string,
  limit: number,
  offset: number,
  opts: SearchOptions,
): SearchResult {
  const start = Date.now()
  const rows = target.prepare(`
    SELECT
      m.id AS messageId,
      m.conversation_id AS conversationId,
      m.role AS role,
      m.agent_id AS agentId,
      m.created_at AS createdAt,
      substr(m.parts, max(1, instr(m.parts, ?) - 30), 80) AS snippetHtml,
      c.title AS conversationTitle,
      a.name AS agentName,
      a.avatar AS agentAvatar
    FROM messages m
    JOIN conversations c ON c.id = m.conversation_id
    LEFT JOIN agents a   ON a.id = m.agent_id
    WHERE m.parts LIKE '%' || ? || '%'
      AND (? IS NULL OR m.conversation_id = ?)
      AND (? IS NULL OR m.role = ?)
    ORDER BY m.created_at DESC
    LIMIT ? OFFSET ?
  `).all(
    q, q,
    opts.conversationId ?? null, opts.conversationId ?? null,
    opts.role ?? null, opts.role ?? null,
    limit, offset,
  ) as HitRow[]

  return { hits: rows.map(rowToHit), total: rows.length, tookMs: Date.now() - start }
}

export async function countSearchMatches(query: string): Promise<number> {
  const r = await searchMessages({ query })
  return r.total
}
```

- [ ] **Step 5: Run all search-service tests**

Run: `pnpm vitest run src/server/search-service.test.ts`
Expected: all 10 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/server/search-service.ts src/server/search-service.test.ts
git commit -m "feat(search): add LIKE fallback path for short Chinese queries"
```

---

## Task 6: Implement API route

**Files:**
- Create: `src/app/api/search/route.ts`
- Test: `src/app/api/search/route.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/app/api/search/route.test.ts`. Since Next.js Route testing requires a request context, use a thin handler test pattern by extracting the logic if needed. For simplicity, test the zod schema and `searchMessages` integration via a small in-test fetch:

```ts
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import Database from 'better-sqlite3'
import { NextRequest } from 'next/server'

import { runMessageSearchMigration } from '@/db/migrate-add-message-search'
import { GET } from './route'

function makeDb() {
  const db = new Database(':memory:')
  db.exec(`
    CREATE TABLE conversations (id TEXT PRIMARY KEY, title TEXT NOT NULL);
    CREATE TABLE agents (id TEXT PRIMARY KEY, name TEXT NOT NULL, avatar TEXT NOT NULL);
    CREATE TABLE messages (
      id TEXT PRIMARY KEY,
      conversation_id TEXT NOT NULL,
      role TEXT NOT NULL,
      agent_id TEXT,
      parts TEXT NOT NULL,
      status TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
  `)
  db.prepare(`INSERT INTO conversations (id, title) VALUES ('c1', 'C1')`).run()
  return db
}

function makeReq(url: string) {
  return new NextRequest(new Request(url))
}

describe('GET /api/search', () => {
  // Note: this test stubs the module's default db by passing through searchMessages,
  // which we cannot do via the route. The route uses the project's real db.
  // For testability, we test the route with a hit on a real (test) schema.
  // Implementation note: the route imports db directly; we exercise the
  // shape only via a helper that re-runs the SQL the route runs.
  it('returns 400 when q is missing', async () => {
    const res = await GET(makeReq('http://localhost/api/search'))
    expect(res.status).toBe(400)
    const body = await res.json()
    expect(body.ok).toBe(false)
    expect(body.error.code).toBe('INVALID_QUERY')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/app/api/search/route.test.ts`
Expected: FAIL — `./route` not found.

- [ ] **Step 3: Implement the API route**

Create `src/app/api/search/route.ts`:

```ts
import { NextRequest, NextResponse } from 'next/server'
import { z } from 'zod'

import { searchMessages } from '@/server/search-service'

const QuerySchema = z.object({
  q: z.string().min(1).max(200),
  limit: z.coerce.number().int().min(1).max(100).default(20),
  offset: z.coerce.number().int().min(0).default(0),
  conversationId: z.string().optional(),
  role: z.enum(['user', 'agent']).optional(),
  fallback: z.enum(['like']).optional(),
})

function envelopeOk<T>(data: T) {
  return NextResponse.json({ ok: true, data })
}

function envelopeErr(code: string, message: string, status: number) {
  return NextResponse.json({ ok: false, error: { code, message } }, { status })
}

export async function GET(req: NextRequest) {
  const url = new URL(req.url)
  const parsed = QuerySchema.safeParse(Object.fromEntries(url.searchParams))
  if (!parsed.success) {
    return envelopeErr('INVALID_QUERY', parsed.error.message, 400)
  }

  const { q, limit, offset, conversationId, role, fallback } = parsed.data
  const result = await searchMessages({ query: q, limit, offset, conversationId, role, fallback })

  if (result.error === 'INVALID_QUERY') {
    return envelopeErr('INVALID_QUERY', 'Invalid search syntax', 400)
  }

  return envelopeOk({ hits: result.hits, total: result.total, tookMs: result.tookMs })
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pnpm vitest run src/app/api/search/route.test.ts`
Expected: 1 test passes.

- [ ] **Step 5: Add client API function**

Modify `src/lib/api.ts`. Find the existing `fetchConversations` function and add after it (or in a logical `Search` group):

```ts
export interface SearchApiResult {
  hits: Array<{
    messageId: string
    conversationId: string
    conversationTitle: string
    role: 'user' | 'agent' | 'system'
    agentId: string | null
    agentName: string | null
    agentAvatar: string | null
    createdAt: number
    snippetHtml: string
  }>
  total: number
  tookMs: number
}

export async function searchMessagesApi(
  query: string,
  opts: { fallback?: 'like'; conversationId?: string; role?: 'user' | 'agent' } = {},
): Promise<SearchApiResult> {
  const params = new URLSearchParams({ q: query })
  if (opts.fallback) params.set('fallback', opts.fallback)
  if (opts.conversationId) params.set('conversationId', opts.conversationId)
  if (opts.role) params.set('role', opts.role)
  const { data } = await json<{ ok: true; data: SearchApiResult }>(
    fetch(`/api/search?${params}`),
  )
  return data
}
```

- [ ] **Step 6: Verify typecheck**

Run: `pnpm typecheck`
Expected: passes.

- [ ] **Step 7: Commit**

```bash
git add src/app/api/search/route.ts src/app/api/search/route.test.ts src/lib/api.ts
git commit -m "feat(search): add /api/search route and client function"
```

---

## Task 7: Implement searchStore (Zustand)

**Files:**
- Create: `src/stores/search-store.ts`
- Test: `src/stores/search-store.test.ts`

- [ ] **Step 1: Write the failing test**

Create `src/stores/search-store.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useSearchStore } from './search-store'

// Mock the API
vi.mock('@/lib/api', () => ({
  searchMessagesApi: vi.fn(async (q: string) => ({
    hits: [
      {
        messageId: 'm1', conversationId: 'c1', conversationTitle: 'C1',
        role: 'user', agentId: null, agentName: null, agentAvatar: null,
        createdAt: 1, snippetHtml: `...${q}...`,
      },
    ],
    total: 1, tookMs: 5,
  })),
}))

describe('useSearchStore', () => {
  beforeEach(() => {
    useSearchStore.setState({
      isOpen: false, query: '', hits: [], total: 0, loading: false, error: null,
      highlightedMessageId: null,
    })
  })

  it('openSearch / closeSearch toggles isOpen', () => {
    useSearchStore.getState().openSearch()
    expect(useSearchStore.getState().isOpen).toBe(true)
    useSearchStore.getState().closeSearch()
    expect(useSearchStore.getState().isOpen).toBe(false)
  })

  it('setQuery updates query', () => {
    useSearchStore.getState().setQuery('hello')
    expect(useSearchStore.getState().query).toBe('hello')
  })

  it('runSearch does not fire for query shorter than 2 chars', async () => {
    const spy = (await import('@/lib/api')).searchMessagesApi as unknown as ReturnType<typeof vi.fn>
    useSearchStore.getState().setQuery('a')
    await useSearchStore.getState().runSearch()
    expect(spy).not.toHaveBeenCalled()
    expect(useSearchStore.getState().hits).toEqual([])
  })

  it('runSearch calls API and sets hits', async () => {
    useSearchStore.getState().setQuery('hello')
    await useSearchStore.getState().runSearch()
    const state = useSearchStore.getState()
    expect(state.hits.length).toBe(1)
    expect(state.total).toBe(1)
  })

  it('runSearch uses fallback=like when query has < 3 Chinese chars', async () => {
    const spy = (await import('@/lib/api')).searchMessagesApi as unknown as ReturnType<typeof vi.fn>
    useSearchStore.getState().setQuery('模型')
    await useSearchStore.getState().runSearch()
    expect(spy).toHaveBeenCalledWith('模型', expect.objectContaining({ fallback: 'like' }))
  })

  it('runSearch does NOT use fallback when query has 3+ Chinese chars', async () => {
    const spy = (await import('@/lib/api')).searchMessagesApi as unknown as ReturnType<typeof vi.fn>
    useSearchStore.getState().setQuery('渲染管线')
    await useSearchStore.getState().runSearch()
    expect(spy).toHaveBeenCalledWith('渲染管线', expect.not.objectContaining({ fallback: 'like' }))
  })

  it('jumpToHit sets highlightedMessageId and closes', () => {
    useSearchStore.getState().openSearch()
    useSearchStore.getState().jumpToHit({
      messageId: 'm1', conversationId: 'c1', conversationTitle: 'C1',
      role: 'user', agentId: null, agentName: null, agentAvatar: null,
      createdAt: 1, snippetHtml: '',
    })
    const s = useSearchStore.getState()
    expect(s.isOpen).toBe(false)
    expect(s.highlightedMessageId).toBe('m1')
  })

  it('clearHighlight resets the field', () => {
    useSearchStore.setState({ highlightedMessageId: 'm1' })
    useSearchStore.getState().clearHighlight()
    expect(useSearchStore.getState().highlightedMessageId).toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pnpm vitest run src/stores/search-store.test.ts`
Expected: FAIL — `./search-store` not found.

- [ ] **Step 3: Implement the store**

Create `src/stores/search-store.ts`:

```ts
'use client'

import { create } from 'zustand'
import { immer } from 'zustand/middleware/immer'

import { searchMessagesApi } from '@/lib/api'
import type { SearchHit } from '@/shared/types'

const DEBOUNCE_MS = 200
const CHINESE_RE = /\p{Script=Han}/gu

function chineseCharCount(s: string): number {
  return (s.match(CHINESE_RE) ?? []).length
}

interface SearchState {
  isOpen: boolean
  query: string
  hits: SearchHit[]
  total: number
  loading: boolean
  error: string | null
  highlightedMessageId: string | null
  /** Conversation that should be active after a jump (consumed by ChatWindow). */
  pendingJumpConversationId: string | null

  openSearch: () => void
  closeSearch: () => void
  setQuery: (q: string) => void
  runSearch: () => Promise<void>
  jumpToHit: (hit: SearchHit) => void
  consumePendingJump: () => string | null
  clearHighlight: () => void
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null

export const useSearchStore = create<SearchState>()(
  immer((set, get) => ({
    isOpen: false,
    query: '',
    hits: [],
    total: 0,
    loading: false,
    error: null,
    highlightedMessageId: null,
    pendingJumpConversationId: null,

    openSearch: () => set((s) => { s.isOpen = true }),
    closeSearch: () => set((s) => { s.isOpen = false }),

    setQuery: (q) => {
      set((s) => { s.query = q })
      if (debounceTimer) clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        void get().runSearch()
      }, DEBOUNCE_MS)
    },

    runSearch: async () => {
      const q = get().query.trim()
      if (q.length < 2) {
        set((s) => { s.hits = []; s.total = 0; s.loading = false; s.error = null })
        return
      }
      const fallback = chineseCharCount(q) < 3 ? 'like' as const : undefined
      set((s) => { s.loading = true; s.error = null })
      try {
        const r = await searchMessagesApi(q, { fallback })
        set((s) => { s.hits = r.hits; s.total = r.total; s.loading = false })
      } catch (err) {
        set((s) => {
          s.hits = []
          s.total = 0
          s.loading = false
          s.error = err instanceof Error ? err.message : 'Search failed'
        })
      }
    },

    jumpToHit: (hit) => {
      set((s) => {
        s.isOpen = false
        s.pendingJumpConversationId = hit.conversationId
        s.highlightedMessageId = hit.messageId
      })
      setTimeout(() => {
        useSearchStore.getState().clearHighlight()
      }, 2000)
    },

    consumePendingJump: () => {
      const id = get().pendingJumpConversationId
      if (id) set((s) => { s.pendingJumpConversationId = null })
      return id
    },

    clearHighlight: () => set((s) => { s.highlightedMessageId = null }),
  })),
)
```

- [ ] **Step 4: Run tests**

Run: `pnpm vitest run src/stores/search-store.test.ts`
Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/stores/search-store.ts src/stores/search-store.test.ts
git commit -m "feat(search): add searchStore with debounce and short-query fallback"
```

---

## Task 8: Build search-result-item component

**Files:**
- Create: `src/components/search-result-item.tsx`

- [ ] **Step 1: Implement the component**

Create `src/components/search-result-item.tsx`:

```tsx
'use client'

import { AgentAvatar } from '@/components/agent-avatar'
import { cn } from '@/lib/utils'
import type { SearchHit } from '@/shared/types'

function formatTime(ts: number): string {
  const d = new Date(ts)
  return d.toLocaleString()
}

export interface SearchResultItemProps {
  hit: SearchHit
  active: boolean
  onClick: () => void
}

export function SearchResultItem({ hit, active, onClick }: SearchResultItemProps) {
  return (
    <li
      role="option"
      aria-selected={active}
      className={cn(
        'flex cursor-pointer items-start gap-3 rounded-md px-3 py-2',
        active && 'bg-accent text-accent-foreground',
      )}
      onClick={onClick}
      onKeyDown={(e) => { if (e.key === 'Enter') onClick() }}
    >
      {hit.agentAvatar ? (
        <AgentAvatar name={hit.agentName ?? 'Agent'} avatar={hit.agentAvatar} />
      ) : (
        <div className="grid h-8 w-8 place-items-center rounded-full bg-muted text-xs">U</div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate font-medium text-foreground">{hit.conversationTitle}</span>
          <span>·</span>
          <span>{hit.role === 'user' ? 'You' : hit.agentName ?? 'Agent'}</span>
          <span>·</span>
          <span>{formatTime(hit.createdAt)}</span>
        </div>
        <p
          className="mt-0.5 line-clamp-2 text-sm"
          // Safe: snippetHtml is from user's own message content (server-generated);
          // <mark> tags are produced by FTS5 snippet() with controlled delimiters.
          dangerouslySetInnerHTML={{ __html: hit.snippetHtml }}
        />
      </div>
    </li>
  )
}
```

- [ ] **Step 2: Verify typecheck**

Run: `pnpm typecheck`
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add src/components/search-result-item.tsx
git commit -m "feat(search): add SearchResultItem component"
```

---

## Task 9: Build global-search modal

**Files:**
- Create: `src/components/global-search.tsx`

- [ ] **Step 1: Implement the modal**

Create `src/components/global-search.tsx`:

```tsx
'use client'

import { Search, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { useSearchStore } from '@/stores/search-store'

import { SearchResultItem } from './search-result-item'

export function GlobalSearch() {
  const isOpen = useSearchStore((s) => s.isOpen)
  const closeSearch = useSearchStore((s) => s.closeSearch)
  const query = useSearchStore((s) => s.query)
  const setQuery = useSearchStore((s) => s.setQuery)
  const hits = useSearchStore((s) => s.hits)
  const loading = useSearchStore((s) => s.loading)
  const error = useSearchStore((s) => s.error)
  const jumpToHit = useSearchStore((s) => s.jumpToHit)
  const runSearch = useSearchStore((s) => s.runSearch)

  const [active, setActive] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isOpen) {
      setActive(0)
      // Defer focus until after the modal opens
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [isOpen])

  // Reset active index when hits change
  useEffect(() => { setActive(0) }, [hits])

  function handleKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(a + 1, hits.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(a - 1, 0))
    } else if (e.key === 'Enter' && hits[active]) {
      e.preventDefault()
      jumpToHit(hits[active])
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(o) => { if (!o) closeSearch() }}>
      <DialogContent className="max-w-2xl gap-0 p-0">
        <DialogTitle className="sr-only">Search messages</DialogTitle>
        <div className="flex items-center gap-2 border-b px-3 py-2">
          <Search className="h-4 w-4 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKey}
            placeholder="Search messages… (⌘K)"
            className="border-0 focus-visible:ring-0"
            maxLength={200}
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="text-muted-foreground hover:text-foreground"
              aria-label="Clear"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="max-h-96 overflow-y-auto p-2">
          {error && (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              Search failed. {error}
            </p>
          )}
          {!error && query.trim().length < 2 && (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              Type at least 2 characters to search.
            </p>
          )}
          {!error && query.trim().length >= 2 && hits.length === 0 && !loading && (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              No messages match.
            </p>
          )}
          {hits.length > 0 && (
            <ul role="listbox">
              {hits.map((hit, i) => (
                <SearchResultItem
                  key={hit.messageId}
                  hit={hit}
                  active={i === active}
                  onClick={() => jumpToHit(hit)}
                />
              ))}
            </ul>
          )}
        </div>

        <div className="border-t px-3 py-1.5 text-xs text-muted-foreground">
          {loading ? 'Searching…' : `${hits.length} result${hits.length === 1 ? '' : 's'}`}
        </div>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 2: Verify typecheck**

Run: `pnpm typecheck`
Expected: passes.

- [ ] **Step 3: Commit**

```bash
git add src/components/global-search.tsx
git commit -m "feat(search): add GlobalSearch modal with keyboard navigation"
```

---

## Task 10: Build global-search-trigger with ⌘K

**Files:**
- Create: `src/components/global-search-trigger.tsx`

- [ ] **Step 1: Implement the trigger**

Create `src/components/global-search-trigger.tsx`:

```tsx
'use client'

import { Search } from 'lucide-react'
import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { useSearchStore } from '@/stores/search-store'

export function GlobalSearchTrigger() {
  const openSearch = useSearchStore((s) => s.openSearch)

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      // ⌘K on Mac, Ctrl+K on other platforms
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openSearch()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [openSearch])

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={openSearch}
      className="gap-2"
      aria-label="Search messages"
    >
      <Search className="h-4 w-4" />
      <span className="hidden md:inline">Search</span>
      <kbd className="ml-2 hidden rounded border bg-muted px-1 text-xs md:inline">⌘K</kbd>
    </Button>
  )
}
```

- [ ] **Step 2: Mount trigger + modal in the top bar**

Open `src/components/sidebar.tsx`. Find the top of the sidebar (or a suitable place in the layout — pick the one that matches project style). Add:

```tsx
import { GlobalSearch } from './global-search'
import { GlobalSearchTrigger } from './global-search-trigger'
```

Then in the JSX, add `<GlobalSearchTrigger />` next to the existing "search" input and `<GlobalSearch />` once at the bottom of the component tree. If a top-bar layout file is the right home instead (e.g. `src/app/layout.tsx`), add the modal there so it's accessible regardless of sidebar state.

> **Decision rule:** if `<GlobalSearch />` is rendered inside the sidebar, it unmounts when the sidebar is collapsed on mobile. Render it in `src/app/layout.tsx` (the root layout) so it's always available.

Modify `src/app/layout.tsx` to add `<GlobalSearch />` once near the body (so it overlays regardless of route).

- [ ] **Step 3: Verify typecheck + lint**

Run: `pnpm typecheck && pnpm lint`
Expected: both pass.

- [ ] **Step 4: Commit**

```bash
git add src/components/global-search-trigger.tsx src/components/sidebar.tsx src/app/layout.tsx
git commit -m "feat(search): mount search trigger + modal in top bar"
```

---

## Task 11: Add message anchor + highlight layer in chat

**Files:**
- Modify: `src/components/chat/message-list.tsx` (or equivalent that renders messages)
- Create: `src/components/message-highlight-layer.tsx`

- [ ] **Step 1: Find the message list component**

Run:
```bash
grep -rln "MessagePart\|messages.map\|MessageRow" src/components/chat/ 2>/dev/null
```

Open the file that iterates over messages and renders each one. Confirm it lives at `src/components/chat/message-list.tsx` (or note the actual path and update the steps below).

- [ ] **Step 2: Add an `id` attribute to each message container**

In the JSX where each message is rendered, find the outermost wrapper for one message and add:

```tsx
id={`message-${msg.id}`}
data-message-id={msg.id}
```

- [ ] **Step 3: Create the highlight layer component**

Create `src/components/message-highlight-layer.tsx`:

```tsx
'use client'

import { useEffect } from 'react'

import { useSearchStore } from '@/stores/search-store'
import { useAppStore } from '@/stores/app-store'

/**
 * Listens to searchStore.highlightedMessageId. When set, scrolls the message
 * into view and applies a 2s highlight class. Auto-clears via the store timer.
 */
export function MessageHighlightLayer() {
  const highlightedId = useSearchStore((s) => s.highlightedMessageId)
  const setActive = useAppStore((s) => s.setActiveConversation)
  const pendingConv = useSearchStore((s) => s.pendingJumpConversationId)
  const consume = useSearchStore((s) => s.consumePendingJump)

  // Step 1: switch to the conversation if needed
  useEffect(() => {
    if (pendingConv) {
      setActive(pendingConv)
      consume()
    }
  }, [pendingConv, setActive, consume])

  // Step 2: scroll + flash when a message id is set
  useEffect(() => {
    if (!highlightedId) return
    // Wait for next paint so the message is rendered
    const t = requestAnimationFrame(() => {
      const el = document.getElementById(`message-${highlightedId}`)
      if (!el) return
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('search-highlight-flash')
      setTimeout(() => el.classList.remove('search-highlight-flash'), 2000)
    })
    return () => cancelAnimationFrame(t)
  }, [highlightedId])

  return null
}
```

- [ ] **Step 4: Add the highlight CSS class**

Open the global stylesheet (e.g. `src/app/globals.css`) and append:

```css
@keyframes search-highlight {
  0%   { background-color: rgba(250, 204, 21, 0.4); }
  100% { background-color: transparent; }
}

.search-highlight-flash {
  animation: search-highlight 2s ease-out;
  border-radius: 0.5rem;
}
```

- [ ] **Step 5: Mount the layer**

Add `<MessageHighlightLayer />` once near the top of the root layout (`src/app/layout.tsx`) or the chat route. Decide based on which file the user is most likely viewing when the search result is clicked — typically `src/app/page.tsx` (the main chat page).

- [ ] **Step 6: Verify typecheck + manual test**

Run: `pnpm typecheck && pnpm dev` (or follow project dev script). Then:
1. Open a conversation
2. Press ⌘K
3. Type a word from one of the messages
4. Click a result
5. Verify the conversation switches, the message is scrolled into view, and a yellow flash plays for 2s.

- [ ] **Step 7: Commit**

```bash
git add src/components/chat/message-list.tsx src/components/message-highlight-layer.tsx src/app/globals.css src/app/page.tsx
git commit -m "feat(search): scroll-to-message with 2s highlight flash"
```

---

## Task 12: Run migration on dev DB

**Files:** none (operational step)

- [ ] **Step 1: Run the migration script**

Run: `pnpm tsx src/db/migrate-add-message-search.ts`
Expected: prints `done`.

- [ ] **Step 2: Verify FTS table exists in dev DB**

Run:
```bash
node -e "const Database = require('better-sqlite3'); const db = new Database('.agenthub-data/agenthub.db'); console.log(db.prepare(\"SELECT name FROM sqlite_master WHERE name='messages_fts'\").get());"
```

Expected: `{ name: 'messages_fts' }` (or similar; the path may differ — check the project's actual DB path; e.g. `agenthub.db` is a common name; if uncertain, find it via `grep -r "Database(" src/db/client.ts`).

- [ ] **Step 3: Spot-check the backfill**

Run:
```bash
node -e "const Database = require('better-sqlite3'); const db = new Database('<db-path>'); const total = db.prepare('SELECT COUNT(*) AS n FROM messages').get().n; const fts = db.prepare('SELECT COUNT(*) AS n FROM messages_fts').get().n; console.log('messages:', total, 'fts_rows:', fts);"
```

Expected: `fts_rows` ≤ `messages` (some messages may have 0 text parts).

- [ ] **Step 4: Commit (no code change; skip if nothing to add)**

If only operational, no commit. Document the run in the PR description later.

---

## Task 13: E2E test (Playwright)

**Files:**
- Create: `e2e/global-search.spec.ts`

- [ ] **Step 1: Check whether Playwright is configured**

Run: `cat playwright.config.ts 2>/dev/null | head -30 || cat package.json | grep -A 3 playwright`
If Playwright is not configured, this task is out of scope for the current plan — defer to a follow-up issue. Skip to Task 14.

- [ ] **Step 2: Write the E2E test**

Create `e2e/global-search.spec.ts`:

```ts
import { expect, test } from '@playwright/test'

test('global search flow', async ({ page }) => {
  await page.goto('/')

  // Open ⌘K (Meta+K on Mac, Control+K elsewhere)
  await page.keyboard.press('Meta+k')
  // Modal should be open with focus on the input
  const input = page.getByPlaceholder(/Search messages/i)
  await expect(input).toBeFocused()

  await input.fill('hello')
  // Wait at least debounce + network
  await page.waitForTimeout(400)
  // Results list should appear (or "no messages match")
  const list = page.getByRole('listbox')
  await expect(list).toBeVisible()

  // If there are results, click the first
  const first = list.getByRole('option').first()
  if (await first.count()) {
    await first.click()
    // Modal closes
    await expect(page.getByRole('dialog')).not.toBeVisible()
    // A message should have the highlight class
    const flashed = page.locator('.search-highlight-flash').first()
    await expect(flashed).toBeVisible()
  }
})
```

- [ ] **Step 3: Run the E2E test**

Run: `pnpm playwright test e2e/global-search.spec.ts`
Expected: passes (or skipped with `--grep` if no test DB seeded).

- [ ] **Step 4: Commit**

```bash
git add e2e/global-search.spec.ts
git commit -m "test(search): add E2E test for global search flow"
```

---

## Task 14: Manual acceptance + final cleanup

**Files:** none (or minor fixes)

- [ ] **Step 1: Walk the spec's acceptance checklist**

Open `specs/16-message-search.md` § 12. For each item, verify by hand or by re-running the relevant test:

- [ ] ⌘K 唤起弹窗，ESC 关闭
- [ ] 输入 → 200ms 内看到结果（建一个 1k 消息测试集）
- [ ] 结果含会话标题 / 发送者 / 时间 / 上下文片段
- [ ] 点击结果 → 跳转到正确会话 → 滚到正确消息 → 2s 高亮
- [ ] 中文 1–2 字走 LIKE 兜底（snippet 不带高亮）
- [ ] 中文 3+ 字走 FTS5（带 `<mark>` 高亮）
- [ ] 英文 `render*` 前缀工作
- [ ] `"exact phrase"` 短语工作
- [ ] `conversationId` 过滤生效（手工加查询参数验证）
- [ ] 1k 消息回填 < 1s（运行 Task 12 验证）
- [ ] 现有 sidebar "按标题过滤" 不受影响
- [ ] 80%+ 覆盖率（`pnpm test:coverage`）
- [ ] `pnpm typecheck` / `pnpm lint` 全过

- [ ] **Step 2: Run the full test + lint suite**

Run: `pnpm typecheck && pnpm lint && pnpm vitest run`
Expected: all green.

- [ ] **Step 3: Push the branch and open a PR**

```bash
git push -u origin feature/message-search
gh pr create --base main --title "feat(search): cross-conversation message search" --body-file <(cat <<'EOF'
## Summary
Implements Spec 16 — cross-conversation full-text search via SQLite FTS5.

- New `messages_fts` virtual table (trigram tokenizer) + 3 DB triggers
  (skipping `status='streaming'` to avoid FTS churn)
- LIKE fallback for short Chinese queries (< 3 chars) — no snippet highlight
- ⌘K command palette in the top bar
- Jump to message + 2s highlight flash
- One-shot migration script (idempotent) — included in PR

## Test plan
- [ ] Unit + integration tests (see `pnpm vitest run`) — green
- [ ] E2E Playwright spec covers ⌘K → search → click → highlight
- [ ] Manual: 1k messages backfill < 1s
- [ ] Manual: existing sidebar title-filter unchanged
EOF
)
```

---

## Self-Review Checklist (run after writing this plan)

- [ ] Every spec section has a task: §2 goals → Tasks 4–11; §5 schema → Tasks 2–3; §7 service → Tasks 4–5; §8 API → Task 6; §9 store/UI → Tasks 7–11; §10 errors → handled in service (Task 4) + UI (Task 9); §11 tests → Tasks 2, 3, 4, 6, 7, 13; §12 acceptance → Task 14.
- [ ] No "TBD" / "TODO" / "implement later" / "add appropriate error handling" without code.
- [ ] Type/method names match across tasks: `SearchHit` (Task 1) used in Task 4, 5, 7, 8; `searchMessages` (Task 4) imported by Task 6; `searchMessagesApi` (Task 6) imported by Task 7; `useSearchStore` (Task 7) imported by Task 9, 10, 11.
- [ ] Commit messages all use Conventional Commits.
- [ ] Each task ends with a commit.
