## ADDED Requirements

### Requirement: Desktop clients SHALL use the same multi-user account model as web

Desktop users MUST register/login through the same account model (`/api/auth/*` semantics, password hashing, token version, etc.). Authentication MUST be performed by the **local engine** against the **primary PostgreSQL** user store. Desktop mode MUST NOT introduce a separate local-only anonymous primary account as the default product path. Desktop mode MUST NOT require a remote official AChat business API process solely to authenticate.

#### Scenario: Desktop login
- **WHEN** a user launches the desktop app and is unauthenticated
- **THEN** they authenticate via the login/register UI served locally
- **AND** the local engine validates credentials against the primary user store
- **AND** issues a user session token usable for subsequent local API calls.

### Requirement: User API keys SHALL be stored with the account and readable by the local engine

API keys saved in user settings MUST be stored with the user account in the primary store. The local engine MUST resolve keys for the authenticated user for direct vendor model calls without a mandatory remote business-API key-fetch hop.

#### Scenario: Key saved once, used on desktop
- **WHEN** a user saves a provider key while logged into their account
- **AND** later starts a custom agent on desktop while online to the primary store
- **THEN** the local engine can resolve the key for model calls without requiring the user to re-enter it as a hard requirement.

### Requirement: Registration invite controls SHALL apply to desktop equally

When registration is disabled or invite-only in the deployment policy enforced by the shared backend/settings, desktop registration MUST observe the same policy as web for that primary store.

#### Scenario: Registration disabled
- **WHEN** registration is disabled by deployment policy on the primary system
- **THEN** desktop users cannot create new accounts against that system either.

### Requirement: Desktop SSE SHALL authenticate users with local engine session semantics

In desktop mode the engine MUST authenticate SSE subscribers using the same user session model as protected REST on the local engine. Engine-token middleware MUST still ensure the caller is the desktop shell session. Desktop streaming MUST NOT require sharing a remote official business API `JWT_SECRET` as the only workable design.

#### Scenario: Desktop EventSource with local user access token
- **WHEN** the desktop frontend opens SSE against the local engine with a valid user access token and a valid engine token
- **THEN** the stream authenticates the user
- **AND** the connection subscribes to that user's in-process event bus
- **AND** Agent stream events published for that user are delivered to the client.

#### Scenario: Desktop SSE without resolvable identity
- **WHEN** desktop mode cannot resolve a user session and engine token checks fail or identity is missing
- **THEN** `/api/stream` returns HTTP 401
- **AND** no event bus subscription is opened for an anonymous desktop client.

#### Scenario: Loopback host alias must not split REST vs SSE auth
- **WHEN** the user has a valid local access token and a valid engine token
- **AND** the UI page host is `localhost` while the engine base host is `127.0.0.1` (same port), or the reverse
- **THEN** protected business REST endpoints on the local engine accept requests that correctly carry both user and engine credentials
- **AND** `/api/stream` also accepts the same identity model (header and/or query engine token as implemented)
- **AND** the system MUST NOT systematically return REST 401 for missing engine token while SSE returns 200 solely because of loopback hostname string mismatch.
