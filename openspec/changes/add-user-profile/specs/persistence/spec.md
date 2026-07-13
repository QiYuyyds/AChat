# Persistence

## MODIFIED Requirements

### Requirement: UserPreference table SHALL distinguish manual and extracted values

The `user_preferences` table MUST include a `source` column (`VARCHAR(16)`, NOT NULL, default `'extracted'`). The column distinguishes values entered manually by the user via the profile UI (`source='manual'`) from values auto-extracted by the LLM or rule-based system (`source='extracted'`). Existing rows at migration time MUST be backfilled to `source='extracted'`.

#### Scenario: Migration adds source column
- **WHEN** the migration runs on an existing database
- **THEN** the `source` column is added with default `'extracted'`
- **AND** all existing rows have `source='extracted'`.

#### Scenario: Manual value is stored
- **WHEN** a user saves a profile field via `PUT /api/profile`
- **THEN** the `UserPreference` row has `source='manual'`.

#### Scenario: Extracted value is stored
- **WHEN** the LLM or rule-based extractor saves a preference
- **THEN** the `UserPreference` row has `source='extracted'`.

#### Scenario: Same key has at most one row
- **WHEN** `Preference.set(key, value, source)` is called
- **THEN** the UPSERT affects a single row for `(user_id, key)`
- **AND** no duplicate rows exist for the same key.
