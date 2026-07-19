## ADDED Requirements

### Requirement: Desktop local engine SHALL be loopback-only by default

In desktop mode, the local engine MUST bind to `127.0.0.1` only. Default product configuration MUST NOT expose the engine on non-loopback interfaces.

#### Scenario: Default desktop bind address
- **WHEN** the local engine starts under desktop shell management
- **THEN** it listens on 127.0.0.1
- **AND** is not advertised as a LAN service in v1.

### Requirement: Desktop local engine SHALL require engine token authentication

Local engine endpoints that can execute tools, start runs, or read local state MUST require a process session engine token issued by the shell/engine bootstrap. User JWT MUST authorize user identity on the engine API; engine token MUST authorize that the caller is the desktop shell session.

#### Scenario: Cross-site call without token fails
- **WHEN** a non-desktop page attempts to call the local engine without the session token
- **THEN** the request is rejected.

### Requirement: Desktop local engine SHALL validate local UI Origin

When an `Origin` header is present on local engine requests, the engine MUST accept only configured allowlisted origins that include the local UI origin. Remote official product website origins MUST NOT be required as the sole allowlisted origins for desktop v1.

#### Scenario: Origin allowlist enforcement
- **WHEN** a request presents an Origin outside the allowlist
- **THEN** the local engine rejects the request even if a token header is malformed or guessed.

### Requirement: Workspace and command safety rules SHALL still apply on desktop

Existing path sandbox checks and command blacklists MUST apply to tool execution performed by the local engine on Windows desktop. Desktop mode MUST NOT weaken blacklist enforcement.

#### Scenario: Banned command on desktop
- **WHEN** a tool attempts a blacklisted Windows command in desktop mode
- **THEN** execution is denied as in the standard platform security rules.

### Requirement: Infrastructure secrets SHALL not be logged

Packaged default and user-overridden infrastructure credentials MUST NOT be written to application logs at info/debug levels in plaintext.

#### Scenario: Engine starts with database URL configured
- **WHEN** the local engine boots with a database URL containing credentials
- **THEN** logs do not print the full credentialed URL
- **AND** failures are reported with redacted connection diagnostics.
