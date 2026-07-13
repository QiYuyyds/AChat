# Design: Add User Profile

## Context

AChat has a multi-user auth system (`user-auth` capability) with a `User` model that includes `avatar_url` — but the field is never populated. The memory system (`memory/preference.py`) stores free-form KV preferences in `UserPreference` (user_id, key, value), auto-extracted from chat via LLM and rule-based matching. There is no way for users to manually enter personal info or set an avatar.

The `ProfileSource` in `prompt_assembler.py` reads `Preference.get_all()` and injects a 【用户画像】 slot into every agent prompt. Manual profile data should flow through this same path so agents immediately benefit without changing the prompt assembly pipeline.

## Goals / Non-Goals

**Goals:**
- Let users fill in 5 structured fields (姓名, 所在地, 家乡, 喜好, 简介) via a UI form.
- Let users upload an avatar image; display it in chat messages and profile UI.
- Map the 5 fields into `UserPreference` so `ProfileSource` picks them up with zero changes.
- Distinguish manual vs extracted values so LLM extraction never silently overwrites user-entered data.
- Extend extraction rules to recognize "家乡" / "老家".

**Non-Goals:**
- Rich social profile features (followers, activity feed, etc.) — this is a local multi-agent tool, not social media.
- Server-side image processing (resize, crop) — the frontend handles client-side cropping/compression before upload.
- OAuth-based profile import (Google avatar, GitHub bio) — out of scope.
- Admin user management UI — covered by `user-auth` capability separately.

## Decisions

### D1: Reuse `UserPreference` table with a new `source` column (no new table)

**Choice**: Add `source` column (`'manual'` | `'extracted'`) to `UserPreference` rather than creating a `UserProfile` table.

**Rationale**: `ProfileSource` already reads `Preference.get_all()`. By writing profile fields into the same table, the entire prompt injection pipeline works unchanged. A separate `UserProfile` table would require a second source or a merge layer in `ProfileSource`, adding complexity for no benefit.

**Alternative considered**: New `UserProfile` table with structured columns (`name`, `location`, `hometown`, `bio`, `preferences`). Rejected because it forces `ProfileSource` to read two stores and merge, and it breaks the existing KV pattern that LLM extraction already uses.

### D2: Manual-wins overwrite strategy (Scheme A — single row per key)

**Choice**: Same `(user_id, key)` has at most one row. `set(key, value, source='manual')` overwrites any existing row. `set(key, value, source='extracted')` is rejected if a `manual` row exists for that key.

**Rationale**: Simpler model — one row per key, no merge logic in `get_all()`. The downside (deleting a manual value doesn't restore the extracted value) is acceptable: extracted values are ephemeral guesses, and the user can always re-mention preferences in chat.

**Alternative considered**: Two-row scheme (manual + extracted per key, manual shadows extracted, delete restores). Rejected as over-engineering for a local single-user tool.

### D3: Avatar stored as file, URL stored in `User.avatar_url`

**Choice**: Avatar images are saved to `<data_dir>/users/{uid}/avatar/{timestamp}.{ext}`. `User.avatar_url` stores the serving path `/api/profile/avatar`. The endpoint serves the most recent file with authentication.

**Rationale**:
- Consistent with the existing `attachment_service` pattern (files on disk, metadata in DB).
- Avoids bloating the `User` row with base64 data (30-100KB per avatar).
- `User.avatar_url` already exists in the schema and is already returned by `/api/auth/me` — no API contract change needed.

**Alternative considered**: Store as base64 data URL in `User.avatar_url`. Rejected for table bloat and inconsistent with existing file storage patterns.

### D4: Exempt `source='manual'` from the 200-char value cap

**Choice**: `_PREFERENCE_VALUE_MAX = 200` applies only to `source='extracted'`. Manual values have no length cap (the "简介" field may exceed 200 chars).

**Rationale**: The cap exists to filter LLM-extracted garbage. User-entered values are trusted by definition.

### D5: Profile fields map to canonical keys via existing synonym system

**Choice**: The 5 form fields map to canonical keys that already exist in `_KEY_SYNONYMS`:

| Form field | Canonical key | Existing extraction rule? |
|---|---|---|
| 姓名 | 姓名 | ✓ "我叫X" |
| 所在地 | 所在地 | ✓ "我在X" |
| 家乡 | 家乡 | ✗ — new rules added |
| 喜好 | 喜好 | ✓ "我喜欢X" |
| 简介 | 简介 | ✗ — no auto-extraction (not a chat-derivable fact) |

New extraction rules in `memory_writer.py`:
- `("我家乡在", "我家乡在", "家乡", True)`
- `("我老家在", "我老家在", "家乡", True)`
- `("老家是", "老家是", "家乡", True)`

`classify_memory_content` extended: "家乡" / "老家" → `category="identity"`, `tags=["hometown"]`, `slot_hint="profile"`.

### D6: Profile API as a separate router, not extending `api/settings.py`

**Choice**: New `api/profile.py` with `GET /api/profile`, `PUT /api/profile`, `POST /api/profile/avatar`, `GET /api/profile/avatar`.

**Rationale**: Settings API handles API keys and deployment config — semantically different from personal info. Separate router keeps concerns clean and avoids bloating the settings patch logic.

### D7: Frontend — new tab in Settings Dialog

**Choice**: Add "个人信息" as the first tab in `settings-dialog.tsx`. Avatar upload uses a clickable preview circle; form fields use `Input` / `Textarea`.

**Rationale**: Settings Dialog already has tabs (keys / companion / deployment). Adding a profile tab is the least-disruptive entry point and avoids a new top-level UI surface.

## Risks / Trade-offs

- **[DB migration]** Adding `source` column to `UserPreference` requires ALTER TABLE. → Migration is additive (column with default `'extracted'`), so old code continues to work. Backfill script sets all existing rows to `'extracted'`.

- **[Avatar file cleanup]** Old avatar files accumulate when users replace their avatar. → On each avatar upload, delete the previous file (if any) before saving the new one. If deletion fails, log and continue (non-blocking).

- **[Concurrent preference writes]** Manual form save and LLM extraction can race on the same key. → `set()` with `source='extracted'` checks for existing manual row inside the same DB transaction. If a manual row appears between check and write, the worst case is a brief window where the extracted value lands — but the UPSERT will update the row, and the next manual save will overwrite it. Acceptable for a local tool.

- **[Avatar auth]** `GET /api/profile/avatar` serves a raw file. Without auth, anyone can fetch avatars. → Endpoint requires `get_current_user` dependency. Avatar URL in `User.avatar_url` is a relative path (`/api/profile/avatar`), not a direct file path, so it always goes through the authenticated endpoint.

## Migration Plan

1. **DB migration**: `ALTER TABLE user_preferences ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'extracted';`
2. **Backfill**: `UPDATE user_preferences SET source = 'extracted' WHERE source IS NULL;` (covered by default).
3. **Code deploy**: Backend + frontend deployed together (new API endpoints + new UI tab).
4. **Rollback**: Drop the `source` column. Old code ignores it. Manual profile data is lost but extracted preferences survive. Avatar files on disk are orphaned but harmless.
