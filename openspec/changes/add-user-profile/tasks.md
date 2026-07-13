## 1. DB Schema: Add `source` column to UserPreference

- [x] 1.1 Add `source` column to `UserPreference` model in `backend/app/db/models.py` (`String(16)`, NOT NULL, default `'extracted'`)
- [x] 1.2 Update `UserPreference.__table_args__` if needed (no new index required — existing `(user_id, key)` PK is sufficient)
- [x] 1.3 Add migration logic to `backend/app/db/engine.py` (or migration script) to `ALTER TABLE user_preferences ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'extracted'` on startup if the column is missing

## 2. Backend: Preference source priority logic

- [x] 2.1 Update `Preference.set()` in `backend/app/memory/preference.py` to accept a `source: str = 'extracted'` parameter
- [x] 2.2 Implement manual-wins logic: when `source='extracted'`, check if existing row has `source='manual'` — if so, reject the write (return without updating)
- [x] 2.3 When `source='manual'`, always UPSERT (overwrite regardless of existing source)
- [x] 2.4 Exempt `source='manual'` values from `_PREFERENCE_VALUE_MAX` truncation in `set()`
- [x] 2.5 Update `extract_and_save_sync()` and `extract_and_save()` to pass `source='extracted'` explicitly
- [x] 2.6 Update `save_batch()` to accept and forward `source` parameter (default `'extracted'`)

## 3. Backend: Memory writer extraction rules

- [x] 3.1 Add hometown extraction rules to `_extract_rule_based()` in `backend/app/memory/memory_writer.py`: `("我家乡在", "我家乡在", "家乡", True)`, `("我老家在", "我老家在", "家乡", True)`, `("老家是", "老家是", "家乡", True)`
- [x] 3.2 Add "家乡" / "老家" classification to `classify_memory_content()`: return `("identity", ["hometown"], "profile")`
- [x] 3.3 Add "家乡" to `_KEY_SYNONYMS` in `preference.py` if needed (no synonyms expected, but verify canonical key consistency)

## 4. Backend: Profile API

- [x] 4.1 Create `backend/app/api/profile.py` with a new `APIRouter`
- [x] 4.2 Implement `GET /api/profile` — read 5 canonical keys from `UserPreference` + `avatar_url` from `User`; return `{ name, location, hometown, preferences, bio, avatarUrl }`
- [x] 4.3 Implement `PUT /api/profile` — accept partial body `{ name?, location?, hometown?, preferences?, bio? }`; for each field: non-null → UPSERT with `source='manual'`, null → DELETE row; return updated profile
- [x] 4.4 Implement `POST /api/profile/avatar` — accept `UploadFile`, validate MIME type (`image/png|jpeg|webp|gif`) and size (≤ 2MB), save to `<data_dir>/users/{uid}/avatar/{timestamp}.{ext}`, delete previous avatar file, update `User.avatar_url = '/api/profile/avatar'`, return `{ avatarUrl }`
- [x] 4.5 Implement `GET /api/profile/avatar` — require auth, look up `User.avatar_url`, if null return 404, else find the latest file in `<data_dir>/users/{uid}/avatar/` and serve with correct `Content-Type`
- [x] 4.6 Register the profile router in `backend/app/main.py`

## 5. Backend: Sync MemoryService preference to use source

- [x] 5.1 Update `MemoryService.on_message_end()` preference extraction calls to pass `source='extracted'`
- [x] 5.2 Update `_safe_llm_extract_preference()` in `memory_service.py` to call `preference.save_batch(prefs, source='extracted')`
- [x] 5.3 Verify `ProfileSource.fetch()` in `prompt_assembler.py` needs no changes (it reads `get_all()` which returns merged values)

## 6. Frontend: Profile API client functions

- [x] 6.1 Add `fetchProfile()` and `updateProfile()` functions to `src/lib/api.ts` (or a new `src/lib/api/profile.ts`)
- [x] 6.2 Add `uploadAvatar(file: File)` function using `FormData` with `POST /api/profile/avatar`
- [x] 6.3 Add `avatarUrl()` helper or constant for the avatar serve path

## 7. Frontend: Settings Dialog — personal info tab

- [x] 7.1 Add a new "个人信息" tab as the first tab in `src/components/settings-dialog.tsx`
- [x] 7.2 Add avatar upload area: clickable circular `Avatar` preview that triggers a hidden `<input type="file" accept="image/*">`, calls `uploadAvatar()` on select
- [x] 7.3 Add 5 form fields with `Input` (姓名, 所在地, 家乡, 喜好) and `Textarea` (简介)
- [x] 7.4 Add "保存" button that calls `updateProfile()` with changed fields only
- [x] 7.5 On successful avatar upload, update `AuthStore.user.avatarUrl` so the UI re-renders

## 8. Frontend: Chat message avatar

- [x] 8.1 Update `src/components/message-item.tsx` user message avatar: use `AvatarImage` with `src={authStore.user?.avatarUrl}` when available, keep `AvatarFallback` as "我"
- [x] 8.2 Verify the avatar renders correctly in both single and group chat modes

## 9. Frontend: AuthStore sync

- [x] 9.1 Add an `updateAvatar(avatarUrl: string)` action to `src/stores/auth-store.ts` that updates `user.avatarUrl` in place
- [x] 9.2 Call `updateAvatar()` after successful avatar upload in the settings dialog

## 10. Testing

- [x] 10.1 Backend test: `PUT /api/profile` writes to `UserPreference` with `source='manual'`
- [x] 10.2 Backend test: LLM extraction with `source='extracted'` does not overwrite a `source='manual'` row
- [x] 10.3 Backend test: `POST /api/profile/avatar` accepts valid image and rejects non-image / oversized
- [x] 10.4 Backend test: `GET /api/profile/avatar` returns 404 when no avatar, returns image when avatar exists
- [x] 10.5 Backend test: hometown extraction rules produce `{ "家乡": "成都" }` from "我老家在成都"
- [x] 10.6 Backend test: manual value exceeding 200 chars is not truncated
- [x] 10.7 Run `ruff check .` and `pytest` in `backend/`
- [x] 10.8 Run `pnpm typecheck` and `pnpm lint` in the frontend

## 11. Spec sync

- [x] 11.1 Update `specs/01-core-entities.md` if it references `UserPreference` columns
- [x] 11.2 Update `backend/app/db/models.py` docstring for `UserPreference` to document the `source` column
- [x] 11.3 Update `CLAUDE.md` §3.2 if the 8 core entities list needs to mention the profile feature
