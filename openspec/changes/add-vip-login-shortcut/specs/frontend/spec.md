# Frontend

## ADDED Requirements

### Requirement: Login page SHALL provide an optional VIP login shortcut

When public auth configuration reports `vipLoginEnabled=true`, the login page MUST display a “VIP 登录” button in the bottom-right corner without changing the existing email/password form. Activating the button MUST open a dialog containing only a password field and login controls.

#### Scenario: VIP shortcut is enabled
- **WHEN** an unauthenticated user opens `/login` and `vipLoginEnabled` is true
- **THEN** the page displays a “VIP 登录” button in the bottom-right corner
- **AND** the existing login form remains unchanged.

#### Scenario: VIP shortcut is disabled
- **WHEN** `vipLoginEnabled` is false
- **THEN** the login page does not render the VIP login button.

#### Scenario: User opens VIP login
- **WHEN** the user activates the “VIP 登录” button
- **THEN** a dialog titled “VIP 登录” opens
- **AND** the dialog displays a password field, cancel control, and login control
- **AND** it does not display the default email, administrator wording, or experience-space wording.

#### Scenario: VIP login succeeds
- **WHEN** the user submits the correct password
- **THEN** the frontend calls `POST /api/auth/vip-login`
- **AND** stores the returned user and access-token mirror through AuthStore
- **AND** redirects to `/`.

#### Scenario: VIP login fails
- **WHEN** the endpoint rejects the password
- **THEN** the dialog remains open
- **AND** the frontend displays a concise password error
- **AND** allows another attempt.

#### Scenario: VIP login is submitted repeatedly
- **WHEN** a VIP login request is in progress
- **THEN** the password field and submit control are disabled
- **AND** no duplicate request is sent.

