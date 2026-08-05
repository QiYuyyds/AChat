## ADDED Requirements

### Requirement: Memory file list SHALL filter by lifecycle stage and digest bucket

`GET /api/memory/files` SHALL return memory files according to the optional `bucket` query parameter. The parameter unifies two orthogonal axes for the management UI: lifecycle stage `daily/` and digest content buckets `procedure` / `wiki`.

- When `bucket` is omitted or null, the response SHALL include all digest files under `digest/` and all daily cards under `daily/`.
- When `bucket` is `procedure`, the response SHALL include only digest files under `digest/procedure/` and SHALL NOT include any daily cards.
- When `bucket` is `wiki`, the response SHALL include only digest files under `digest/wiki/` and SHALL NOT include any daily cards.
- When `bucket` is `daily`, the response SHALL include only daily cards under `daily/` and SHALL NOT include any digest files.
- Daily cards in the response SHALL use the synthetic display field `bucket: "daily"` (not a frontmatter-legal value).
- Digest files in the response SHALL use their frontmatter `bucket` (`procedure` or `wiki`).

#### Scenario: List all categories without bucket

- **WHEN** a client calls `GET /api/memory/files` without a `bucket` parameter
- **THEN** the response includes digest files from both procedure and wiki
- **AND** the response includes daily cards from `daily/`

#### Scenario: Filter by procedure excludes daily

- **WHEN** a client calls `GET /api/memory/files?bucket=procedure`
- **THEN** every returned item has `bucket` equal to `procedure`
- **AND** no returned item has path under `daily/`
- **AND** no returned item has `bucket` equal to `daily` or `wiki`

#### Scenario: Filter by wiki excludes daily

- **WHEN** a client calls `GET /api/memory/files?bucket=wiki`
- **THEN** every returned item has `bucket` equal to `wiki`
- **AND** no returned item has path under `daily/`
- **AND** no returned item has `bucket` equal to `daily` or `procedure`

#### Scenario: Filter by daily excludes digest

- **WHEN** a client calls `GET /api/memory/files?bucket=daily`
- **THEN** every returned item has `bucket` equal to `daily`
- **AND** every returned item path is under `daily/`
- **AND** no digest procedure or wiki files are returned

### Requirement: Unknown bucket values SHALL yield an empty list

When `bucket` is present and is not one of `procedure`, `wiki`, or `daily`, the list endpoint SHALL return an empty `items` array (HTTP 200) rather than falling back to the full listing.

#### Scenario: Unknown bucket does not leak full listing

- **WHEN** a client calls `GET /api/memory/files?bucket=personal`
- **THEN** the response `items` array is empty
- **AND** the status code is 200
