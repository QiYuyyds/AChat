## ADDED Requirements

### Requirement: CustomAgentAdapter ReAct path SHALL support model-done and tool-less final turns

CustomAgentAdapter (and the SDK ReAct path that drives it) MUST support continued looping until the model returns no tool calls, and MUST support a forced final invocation in which no tools are offered (or tool calls are ignored) so a natural-language summary can be produced.

#### Scenario: Zero tool calls ends the adapter-driven turn loop
- **WHEN** the provider response contains no function/tool calls
- **THEN** the Custom adapter path yields the final text events
- **AND** does not require a remaining step quota to treat the run as successfully finished

#### Scenario: Forced final without tools
- **WHEN** the runner requests a forced final model call
- **THEN** the Custom path invokes the provider with an empty tool list (or equivalent)
- **AND** emits user-visible assistant text without executing tools from that response

### Requirement: Custom path MUST NOT rely on a small default MAX_TURNS product cap

The Custom adapter/runner MUST NOT encode a small default such as `MAX_TURNS = 8` as the normal success boundary for Custom agents. An optional high `max_tool_turns` may exist only as an explicit fuse handled by the shared wrap-up pipeline.

#### Scenario: Unconfigured Custom long run
- **WHEN** a Custom agent needs more than eight tool-use iterations and has not hit budget/circuit breakers
- **THEN** the adapter/runner continues while the model emits tool calls
- **AND** does not silently stop at eight iterations
