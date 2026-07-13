# Frontend

## ADDED Requirements

### Requirement: Settings dialog SHALL include a personal info tab

The Settings Dialog MUST include a "个人信息" tab as the first tab. The tab MUST display: an avatar upload area (clickable circular preview), and 5 form fields (姓名, 所在地, 家乡, 喜好, 简介). The "简介" field MUST use a multi-line textarea; others use single-line inputs. A "保存" button MUST submit the form via `PUT /api/profile`.

#### Scenario: User opens the profile tab
- **WHEN** the user opens Settings and clicks the "个人信息" tab
- **THEN** the frontend fetches the current profile via `GET /api/profile`
- **AND** populates each field with the returned value (or empty if null).

#### Scenario: User saves profile fields
- **WHEN** the user edits fields and clicks "保存"
- **THEN** the frontend sends `PUT /api/profile` with only the changed fields
- **AND** on success, shows a confirmation indicator.

#### Scenario: User uploads an avatar
- **WHEN** the user clicks the avatar preview and selects an image file
- **THEN** the frontend sends `POST /api/profile/avatar` (multipart/form-data)
- **AND** on success, updates the avatar preview and the AuthStore's `user.avatarUrl`.

### Requirement: Chat messages SHALL display the user's avatar

User messages in the chat UI (`message-item.tsx`) MUST display the authenticated user's avatar image when available. When `avatarUrl` is null, the current "我" text fallback MUST be used.

#### Scenario: User with avatar sends a message
- **WHEN** a user with a non-null `avatarUrl` sends a message
- **THEN** the message avatar renders the user's avatar image via `<AvatarImage>`.

#### Scenario: User without avatar sends a message
- **WHEN** a user with a null `avatarUrl` sends a message
- **THEN** the message avatar falls back to the "我" text in `<AvatarFallback>`.

### Requirement: AuthStore SHALL refresh avatar after profile updates

After a successful avatar upload or profile update, the AuthStore MUST update `user.avatarUrl` so that all UI components referencing the user's avatar re-render with the new image.

#### Scenario: Avatar upload updates AuthStore
- **WHEN** `POST /api/profile/avatar` succeeds
- **THEN** `AuthStore.user.avatarUrl` is updated to the new avatar URL
- **AND** all components displaying the user's avatar reflect the change.
