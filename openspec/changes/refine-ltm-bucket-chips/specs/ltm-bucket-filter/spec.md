## ADDED Requirements

### Requirement: Long-term memory type filter uses chips

The long-term memory panel SHALL present the memory type filter as a single-select chip row below the search controls, not as a dropdown. Chips MUST be: 全部 (`all`), 经验 (`procedure`), 知识 (`wiki`), 日常 (`daily`). Chips MUST NOT display item counts.

#### Scenario: Default shows all types

- **WHEN** the user opens the long-term memory panel
- **THEN** the 全部 chip is selected and the list loads without a bucket filter

#### Scenario: Selecting a type filters the list

- **WHEN** the user clicks 经验 / 知识 / 日常
- **THEN** that chip becomes the only selected chip and the list reloads with the corresponding `bucket` query parameter

#### Scenario: Selecting 全部 clears the type filter

- **WHEN** the user clicks 全部 while another type chip is selected
- **THEN** 全部 is selected and the list reloads without a bucket filter

### Requirement: Chip colors match existing bucket visual language

Type chips for 经验 / 知识 / 日常 MUST reuse the same color tokens as memory cards and interest-topic pills for `procedure` / `wiki` / `daily` (blue / emerald / amber via the shared bucket config). The 全部 chip MUST use a neutral selected style. The panel MUST NOT introduce a separate color palette for chips.

#### Scenario: Selected type chip matches card accent

- **WHEN** the user selects 知识
- **THEN** the 知识 chip selected appearance uses the wiki/emerald styling shared with knowledge memory cards

### Requirement: Search and type chips interaction

While search mode is active, changing a type chip MUST exit search mode and reload the list for the selected bucket. Clearing search MUST reload using the currently selected chip’s bucket (or no bucket when 全部 is selected).

#### Scenario: Clicking a chip exits search

- **WHEN** search mode is active and the user clicks a type chip
- **THEN** search mode is cleared (or equivalent non-search load path is used) and the list loads for that chip’s bucket

#### Scenario: Clearing search restores chip filter

- **WHEN** the user clears search while a non-全部 chip is selected
- **THEN** the list reloads filtered by that chip’s bucket

### Requirement: Toolbar no longer uses type dropdown or agent id field

The long-term memory filter toolbar MUST NOT render a type `<select>` (including the label 全部分类) and MUST NOT render an Agent ID text input in the main filter bar.

#### Scenario: Main bar controls

- **WHEN** the user views the long-term memory panel filter area
- **THEN** they see search, type chips, refresh, and manual refine actions, without a type dropdown or Agent ID field
