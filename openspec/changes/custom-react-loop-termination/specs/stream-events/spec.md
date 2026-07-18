## ADDED Requirements

### Requirement: Run completion events SHALL carry optional stop reason fields

When a Custom agent run ends, stream/persistence completion events MUST be able to include optional camelCase fields describing why the run stopped, for example `stopReason` (machine enum) and `stopReasonLabel` (short user-facing Chinese text). Absence of these fields MUST remain valid for older clients and non-Custom runs.

#### Scenario: Forced final run end event
- **WHEN** a Custom run ends after a forced final summary
- **THEN** the run completion event includes a non-empty `stopReason` identifying forced/budget/breaker termination
- **AND** includes a `stopReasonLabel` suitable for light UI display

#### Scenario: Backward compatible omission
- **WHEN** a CLI agent run ends without Custom termination metadata
- **THEN** clients MUST accept the event without `stopReason` / `stopReasonLabel`
- **AND** MUST NOT fail stream processing
