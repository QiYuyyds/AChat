# Capability: execution-plan-analytics

## ADDED Requirements

### Requirement: plan-usage-stats-in-run

When a run ends, if the run created an execution plan, the system SHALL write plan usage statistics into the `agent_runs.usage` JSONB column under a `plan` key:

```json
{
  "plan": {
    "created": true,
    "complexity": "moderate",
    "stepCount": 4,
    "completedSteps": 3,
    "skippedSteps": 1,
    "addedStepsCount": 1
  }
}
```

For runs without a plan, the `plan` key SHALL be absent (not `{"created": false}`).

#### Scenario: Run with plan writes stats
- **WHEN** a run ends that created a plan with 4 steps (3 done, 1 skipped, 1 added via add_plan_steps)
- **THEN** the `agent_runs.usage` JSONB SHALL contain a `plan` key with `{"created": true, "stepCount": 5, "completedSteps": 3, "skippedSteps": 1, "addedStepsCount": 1, ...}`

#### Scenario: Run without plan omits stats
- **WHEN** a run ends that did not create any execution plan
- **THEN** the `agent_runs.usage` JSONB SHALL NOT contain a `plan` key

### Requirement: plan-usage-stats-api

The system SHALL expose a `GET /api/plan-usage/stats` endpoint returning aggregated plan usage statistics:

```json
{
  "totalRuns": 1000,
  "withPlan": 300,
  "withoutPlan": 700,
  "complexityDistribution": { "simple": 50, "moderate": 180, "complex": 70 },
  "avgStepCount": { "simple": 2.1, "moderate": 4.3, "complex": 6.7 },
  "completionRate": { "simple": 0.95, "moderate": 0.82, "complex": 0.71 },
  "avgAddedSteps": 0.3
}
```

#### Scenario: Stats API returns aggregated data
- **WHEN** a GET request is made to `/api/plan-usage/stats`
- **THEN** the response SHALL contain aggregated plan usage metrics computed from `agent_runs.usage` data

#### Scenario: Stats API requires authentication
- **WHEN** an unauthenticated request is made to `/api/plan-usage/stats`
- **THEN** the response SHALL be 401 Unauthorized

### Requirement: plan-usage-stats-scope

Plan usage statistics SHALL be scoped to `user_id` — each user SHALL only see statistics for their own runs. Admin users SHALL see global statistics.

#### Scenario: Regular user sees own stats
- **WHEN** a non-admin user requests plan usage stats
- **THEN** the statistics SHALL be computed only from runs belonging to that user

#### Scenario: Admin sees global stats
- **WHEN** an admin user requests plan usage stats
- **THEN** the statistics SHALL be computed from all runs globally
