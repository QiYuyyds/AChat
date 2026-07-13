# Add User Profile

## Why

Users currently have no way to manually provide personal information (name, location, hometown, preferences, bio) or set an avatar. The system relies solely on LLM-based extraction from chat messages, which is unreliable, slow, and gives users no control. Additionally, the `User.avatar_url` field exists in the schema but is never populated or displayed — user messages in chat show a hardcoded "我" text fallback instead of an actual avatar.

## What Changes

- Add a **Profile API** (`GET/PUT /api/profile`) for reading and writing 5 structured personal info fields (姓名, 所在地, 家乡, 喜好, 简介), persisted to `UserPreference` with `source='manual'`.
- Add **avatar upload** (`POST /api/profile/avatar`) and **avatar serving** (`GET /api/profile/avatar`) endpoints, storing image files under `<data_dir>/users/{uid}/avatar/`.
- Add a `source` column (`'manual'` | `'extracted'`) to `UserPreference` to distinguish user-entered values from LLM-extracted ones. Manual values take priority — LLM extraction (`source='extracted'`) cannot overwrite a key that already has a `source='manual'` value.
- **BREAKING** (DB schema): `UserPreference` table gains a `source` column with default `'extracted'` for backward compatibility. Existing rows are backfilled to `'extracted'`.
- Extend preference extraction rules in `memory_writer.py` to recognize "家乡" / "老家" patterns.
- Add a **"个人信息" tab** to the Settings Dialog with avatar upload and 5 form fields.
- Update **chat message avatar** rendering (`message-item.tsx`) to display the user's avatar image instead of the hardcoded "我" fallback.
- Update `auth-store.ts` to refresh `avatarUrl` after profile updates so the UI stays in sync.
- Exempt `source='manual'` preference values from the 200-character length cap (so "简介" can be longer).

## Capabilities

### New Capabilities
- `user-profile`: User-editable personal information (structured fields + avatar) with mapping to the memory/preference system.

### Modified Capabilities
- `user-auth`: `User.avatar_url` is now actively populated and served via a new endpoint; the `/api/auth/me` response already includes `avatarUrl` but the value will now be non-null.
- `persistence`: `UserPreference` table gains a `source` column; migration backfills existing rows.
- `frontend`: Settings Dialog gains a "个人信息" tab; chat message rendering uses user avatar image.

## Impact

- **Backend DB**: `UserPreference` schema change (new `source` column + migration). No new tables.
- **Backend API**: New `api/profile.py` router with 3 endpoints. `api/auth.py` `/me` endpoint unchanged (already returns `avatar_url`).
- **Backend Memory**: `preference.py` `set()` / `get_all()` logic changes for source priority. `memory_writer.py` new extraction rules for "家乡".
- **Frontend**: `settings-dialog.tsx` new tab; `message-item.tsx` avatar rendering; `auth-store.ts` profile refresh; `lib/api.ts` new API functions.
- **File system**: New `<data_dir>/users/{uid}/avatar/` directory for avatar image storage.
- **No new dependencies**: Avatar upload reuses existing FastAPI `UploadFile`; file storage follows the existing attachment pattern.
