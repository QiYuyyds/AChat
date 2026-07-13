# User Profile

## ADDED Requirements

### Requirement: Users SHALL read and write personal profile fields

AChat MUST provide `GET /api/profile` and `PUT /api/profile` endpoints for reading and writing 5 structured personal info fields: `name` (姓名), `location` (所在地), `hometown` (家乡), `preferences` (喜好), `bio` (简介). All fields are optional and nullable. The endpoints MUST require authentication via `get_current_user`.

#### Scenario: User reads their profile
- **WHEN** an authenticated user sends `GET /api/profile`
- **THEN** AChat returns a JSON object with keys `name`, `location`, `hometown`, `preferences`, `bio`, and `avatarUrl`
- **AND** each field contains the current value or `null` if unset.

#### Scenario: User updates profile fields
- **WHEN** an authenticated user sends `PUT /api/profile` with any subset of `{ name, location, hometown, preferences, bio }`
- **THEN** AChat stores each provided field into `UserPreference` with `source='manual'`
- **AND** fields not included in the request body remain unchanged
- **AND** AChat returns the updated profile object.

#### Scenario: User clears a profile field
- **WHEN** a `PUT /api/profile` request includes a field with value `null`
- **THEN** AChat deletes the corresponding `UserPreference` row for that key
- **AND** subsequent `GET /api/profile` returns `null` for that field.

### Requirement: Profile fields SHALL map to UserPreference canonical keys

Each profile form field MUST map to a canonical key in the `UserPreference` table:

| Form field | Canonical key |
|---|---|
| name | 姓名 |
| location | 所在地 |
| hometown | 家乡 |
| preferences | 喜好 |
| bio | 简介 |

When writing via `PUT /api/profile`, the value MUST be stored with `source='manual'`. When reading via `GET /api/profile`, the API MUST read from `UserPreference` using these canonical keys.

#### Scenario: Profile field is written to UserPreference
- **WHEN** a user saves `location: "北京"` via `PUT /api/profile`
- **THEN** a `UserPreference` row is created with `key='所在地'`, `value='北京'`, `source='manual'`.

#### Scenario: Profile data flows to prompt injection
- **WHEN** an agent run triggers `ProfileSource.fetch()`
- **THEN** the profile fields appear in the 【用户画像】 slot of the system prompt
- **AND** no changes to `ProfileSource` or `PromptAssembler` are required.

### Requirement: Manual profile values SHALL take priority over extracted values

When `Preference.set()` is called with `source='extracted'` for a key that already has a `source='manual'` row, the write MUST be rejected and the manual value preserved. When called with `source='manual'`, the value MUST overwrite any existing row for that key regardless of source.

#### Scenario: LLM extraction tries to overwrite a manual value
- **WHEN** `Preference.set('所在地', '重庆', source='extracted')` is called
- **AND** a `UserPreference` row exists with `key='所在地'`, `source='manual'`, `value='北京'`
- **THEN** the write is rejected
- **AND** the existing manual value `'北京'` is preserved.

#### Scenario: User manually overwrites an extracted value
- **WHEN** `Preference.set('喜好', 'Rust', source='manual')` is called
- **AND** a `UserPreference` row exists with `key='喜好'`, `source='extracted'`, `value='Python'`
- **THEN** the row is updated to `value='Rust'`, `source='manual'`.

#### Scenario: LLM extraction writes to a key with no manual value
- **WHEN** `Preference.set('喜好', 'Python', source='extracted')` is called
- **AND** no `UserPreference` row exists for that key
- **THEN** a new row is created with `source='extracted'`.

### Requirement: Manual preference values SHALL be exempt from the length cap

The `_PREFERENCE_VALUE_MAX` (200 characters) length restriction MUST apply only to values with `source='extracted'`. Values with `source='manual'` MUST NOT be truncated.

#### Scenario: User saves a long bio
- **WHEN** a user saves `bio` with 300 characters via `PUT /api/profile`
- **THEN** the full 300-character value is stored in `UserPreference` without truncation.

#### Scenario: LLM extracts a long value
- **WHEN** `Preference.set('喜好', value_exceeding_200_chars, source='extracted')` is called
- **THEN** the value is truncated to 200 characters as before.

### Requirement: Users SHALL upload an avatar image

AChat MUST provide `POST /api/profile/avatar` (multipart/form-data, field `file`) for avatar upload. The endpoint MUST accept only image MIME types (`image/png`, `image/jpeg`, `image/webp`, `image/gif`) with a maximum file size of 2MB. The uploaded image MUST be stored under `<data_dir>/users/{user_id}/avatar/{timestamp}.{ext}`. The `User.avatar_url` column MUST be updated to `/api/profile/avatar`. On replacement, the previous avatar file MUST be deleted.

#### Scenario: User uploads a valid avatar
- **WHEN** an authenticated user uploads a 500KB PNG via `POST /api/profile/avatar`
- **THEN** the image is saved to `<data_dir>/users/{uid}/avatar/{timestamp}.png`
- **AND** `User.avatar_url` is set to `/api/profile/avatar`
- **AND** the response returns the updated `avatarUrl`.

#### Scenario: User replaces their avatar
- **WHEN** a user with an existing avatar uploads a new image
- **THEN** the previous avatar file is deleted from disk
- **AND** the new image is saved
- **AND** `User.avatar_url` remains `/api/profile/avatar` (stable URL).

#### Scenario: Non-image file is rejected
- **WHEN** a user uploads a `.txt` file
- **THEN** AChat returns HTTP 400 with an error message.

#### Scenario: Oversized file is rejected
- **WHEN** a user uploads a 5MB image
- **THEN** AChat returns HTTP 413 with an error message.

### Requirement: Avatar SHALL be served via an authenticated endpoint

AChat MUST provide `GET /api/profile/avatar` to serve the current user's avatar image. The endpoint MUST require authentication. If the user has no avatar, it MUST return HTTP 404.

#### Scenario: Authenticated user fetches their avatar
- **WHEN** an authenticated user sends `GET /api/profile/avatar`
- **AND** the user has an avatar file
- **THEN** AChat returns the image bytes with the correct `Content-Type` header.

#### Scenario: User has no avatar
- **WHEN** an authenticated user sends `GET /api/profile/avatar`
- **AND** `User.avatar_url` is null
- **THEN** AChat returns HTTP 404.

### Requirement: Preference extraction SHALL recognize hometown patterns

The rule-based preference extraction in `memory_writer.py` MUST recognize the following patterns and extract them as canonical key "家乡":
- "我家乡在X"
- "我老家在X"
- "老家是X"

The `classify_memory_content` function MUST classify "家乡" / "老家" content as `category="identity"`, `tags=["hometown"]`, `slot_hint="profile"`.

#### Scenario: User mentions hometown in chat
- **WHEN** a user sends "我老家在成都"
- **THEN** the rule-based extractor produces `{ "家乡": "成都" }`
- **AND** `classify_memory_content` returns `("identity", ["hometown"], "profile")`.

#### Scenario: User mentions hometown with a longer sentence
- **WHEN** a user sends "我家乡在重庆，那里有很多美食"
- **THEN** the extractor produces `{ "家乡": "重庆" }` (truncated at first sentence separator).
