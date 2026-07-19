## ADDED Requirements

### Requirement: Desktop clients SHALL use the same account system as web

Desktop users MUST register/login through the same official `/api/auth/*` account system. Desktop mode MUST NOT introduce a separate local-only anonymous primary account as the default product path.

#### Scenario: Desktop login
- **WHEN** a user launches the desktop app and is unauthenticated
- **THEN** they authenticate via the official login/register flow
- **AND** receive the same JWT/cookie session model as web, subject to webview cookie capabilities.

### Requirement: User API keys SHALL sync with the account for desktop engine use

API keys saved in user settings MUST be stored on the official server with the user account. The local engine MUST be able to retrieve keys for the authenticated user over HTTPS when online to perform direct vendor model calls.

#### Scenario: Key saved on web, used on desktop
- **WHEN** a user saves a provider key while logged into the same account on web
- **AND** later uses desktop with that account online
- **THEN** the local engine can resolve the key for model calls without requiring the user to re-enter it on that machine as a hard requirement.

### Requirement: Registration invite controls SHALL apply to desktop equally

When cloud registration is disabled or invite-only, desktop registration MUST observe the same server policy as web.

#### Scenario: Registration disabled
- **WHEN** `ALLOW_REGISTRATION=false` (or equivalent invite-only mode) is enforced by the official server
- **THEN** desktop users cannot create new accounts against that server either.

### Requirement: Desktop SSE SHALL resolve users via official identity, not local JWT_SECRET

In desktop mode the local engine's `JWT_SECRET` MAY differ from the official API (and MAY be random for residual local tokens only). The engine MUST NOT authenticate desktop SSE subscribers solely by verifying the official access token with the local secret. Desktop `/api/stream` MUST resolve the user with the same cloud-validated path as REST (`resolve_desktop_user` / official `/api/auth/me` + local shadow User), after engine-token middleware has accepted the caller.

#### Scenario: Desktop EventSource with official access token
- **WHEN** the desktop frontend opens SSE against the local engine with a valid official user access token (`?token=`) and a valid engine token
- **AND** the engine's local `JWT_SECRET` is not the official signing secret
- **THEN** the stream still authenticates the user
- **AND** the connection subscribes to that user's in-process event bus
- **AND** Agent stream events published for that user are delivered to the client.

#### Scenario: Desktop SSE without resolvable identity
- **WHEN** desktop mode cannot resolve a user via cloud session/token and local JWT verification also fails
- **THEN** `/api/stream` returns HTTP 401
- **AND** no event bus subscription is opened for an anonymous desktop client.
