# Spec Delta: Orchestrator

## ADDED Requirements

### Requirement: Plan stage SHALL require workspace exploration before planning

The orchestrator plan prompt MUST instruct the LLM to explore the workspace structure using read-only tools before calling `plan_tasks`, and the plan MUST declare which files were explored.

#### Scenario: Plan includes explored files

- **WHEN** the orchestrator calls `plan_tasks`
- **THEN** the plan reasoning includes an `explored` list of file paths inspected before planning
- **AND** the plan reasoning includes a `complexity` assessment (`simple`, `moderate`, or `complex`).

#### Scenario: Code task plan follows exploration

- **WHEN** the user requests a code implementation task
- **THEN** the orchestrator uses `fs_list` and `fs_read` to inspect the workspace before planning
- **AND** the plan tasks reference the explored file structure.

#### Scenario: Simple task is not over-decomposed

- **WHEN** the orchestrator assesses the task as `simple` (single-file change, straightforward logic)
- **THEN** the plan contains a single task without unnecessary decomposition
- **AND** no artificial parallel tasks are created.

### Requirement: DAG execution SHALL prioritize longer tasks within a wave

When multiple tasks are ready to execute in the same DAG wave, AgentRunner MUST sort them by estimated duration (longer first) before launching, so that long-running tasks start earlier and reduce total wall-clock time.

#### Scenario: Code and review tasks in same wave

- **WHEN** a wave contains a code implementation task and a review task
- **THEN** the code task is launched before the review task
- **AND** both still run concurrently within the wave.

#### Scenario: Tasks with target_paths are prioritized

- **WHEN** a wave contains tasks with and without `targetPaths`
- **THEN** tasks with `targetPaths` are launched first within the wave.

### Requirement: Aggregation SHALL analyze cross-task consistency and completion

The aggregate prompt MUST instruct the orchestrator to check consistency across task outputs, score overall completion, and recommend next steps.

#### Scenario: Aggregate checks cross-task consistency

- **WHEN** the orchestrator runs the aggregate stage
- **THEN** the prompt instructs it to identify contradictions or conflicts between task outputs
- **AND** surface them in the summary message.

#### Scenario: Aggregate scores completion

- **WHEN** the aggregate stage produces the final summary
- **THEN** the summary includes a completion assessment (fully complete / partially complete / failed)
- **AND** explains what is missing if partially complete.

#### Scenario: Aggregate recommends next steps

- **WHEN** the aggregate stage identifies failed or skipped tasks
- **THEN** the summary includes specific next-step recommendations
- **AND** does not claim partial success as full success.

## MODIFIED Requirements

### Requirement: Orchestrator SHALL plan before dispatch

The orchestration flow MUST produce a compiled and validated task plan before launching child agent runs. The plan stage MUST instruct the LLM to explore the workspace before planning and to assess task complexity.

#### Scenario: Plan tool is called

- **WHEN** the orchestrator calls `plan_tasks`
- **THEN** AgentRunner parses, compiles, and validates task ids, agent ids, dependencies, and acyclicity
- **AND** the plan reasoning MAY include advisory `complexity` and `explored` fields (not validated).

#### Scenario: Plan text implies missing dependencies

- **WHEN** task text references earlier task outputs but `dependsOn` omits them
- **THEN** AgentRunner adds high-confidence inferred dependencies before dispatch
- **AND** publishes and executes the compiled plan.

#### Scenario: Local workspace code project is requested

- **WHEN** the conversation workspace is local
- **AND** the user asks to create, modify, initialize, debug, or build project source files
- **THEN** the plan prompt tells the orchestrator to explore the workspace first
- **AND** the plan prompt tells the orchestrator to prefer agents with file/command tools
- **AND** the plan should use `acceptanceCriteria` for local file and command outcomes instead of `expectedOutputs`.

#### Scenario: Code task contract is normalized

- **WHEN** the compiled plan contains a code implementation task
- **THEN** AgentRunner ensures the task has a required `project` expected output
- **AND** ensures the task has acceptance/evidence requirements for a successful runnable verification command.

### Requirement: Aggregation SHALL summarize child outputs

After child tasks finish, the orchestrator MUST run an aggregate stage that sees task results and produces the final response. The aggregate prompt MUST instruct the orchestrator to analyze cross-task consistency, score overall completion, and recommend next steps.

#### Scenario: All child tasks complete

- **WHEN** the DAG has no remaining runnable tasks
- **THEN** AgentRunner builds an aggregate prompt with consistency-check, completion-scoring, and next-step instructions
- **AND** runs the orchestrator without `plan_tasks`.

#### Scenario: Some tasks failed or skipped

- **WHEN** the aggregate prompt includes failed or skipped task results
- **THEN** the orchestrator summary explicitly states the task is not fully complete
- **AND** recommends specific remediation steps.
