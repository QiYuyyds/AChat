## ADDED Requirements

### Requirement: AgentHub SHALL provide a Tavily-backed web_search tool

AgentHub MUST register a `web_search` tool in `toolRegistry` that queries the Tavily Search API and returns results to the calling agent. The tool MUST accept a required string `query` parameter and return Tavily's synthesized answer (when present) plus a bounded list of search results, each containing at least title, URL, and a content snippet. The tool MUST consume the Tavily API key from configuration sourced from the `TAVILY_API_KEY` environment variable (env-fallback layer); the key MUST NOT be hardcoded.

#### Scenario: Custom agent enables and uses web search
- **WHEN** a custom agent whose `toolNames` includes `web_search` calls it with a `query`
- **THEN** AgentHub resolves the tool from `toolRegistry`
- **AND** issues a Tavily search request authenticated with the configured key
- **AND** returns the answer (if any) and at most 5 results to the agent.

#### Scenario: Web search is opt-in and never auto-injected
- **WHEN** a custom agent's `toolNames` does not include `web_search`
- **THEN** the agent does NOT have access to `web_search`
- **AND** AgentHub does NOT implicitly inject it (unlike `memory_recall` or RAG tools).

#### Scenario: SDK agents are out of scope
- **WHEN** an agent uses an SDK adapter (`claude` or `codex`)
- **THEN** `web_search` is not available because SDK agents' `toolNames` are forced empty and they use their own builtin tool set.

#### Scenario: Missing API key
- **WHEN** `web_search` is invoked but no Tavily API key is configured
- **THEN** the tool returns an error result describing the missing key
- **AND** does NOT crash the agent run or the server.

#### Scenario: Run is cancelled during search
- **WHEN** the run's cancel signal is set while a Tavily request is in flight
- **THEN** the tool stops waiting and returns an error result instead of blocking.

#### Scenario: Results are bounded
- **WHEN** Tavily returns more than 5 results or long content snippets
- **THEN** the tool limits the returned results to at most 5
- **AND** truncates each result's content to a bounded length to protect the model context.

#### Scenario: No approval gate
- **WHEN** an agent calls `web_search`
- **THEN** the tool executes directly without recording a pending approval (parity with `rag_search`), because it performs a read-only external lookup with no host, file, or dependency mutation.
