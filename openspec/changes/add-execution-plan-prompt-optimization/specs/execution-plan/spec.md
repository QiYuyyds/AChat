# Capability: execution-plan (delta — prompt optimization)

## MODIFIED Requirements

### Requirement: plan-prompt-guidance

The system prompt for plan tools SHALL be optimized with:

1. **Specific boundary conditions** replacing the vague "3步以上" rule:
   - **DO use create_plan** when: modifying 3+ files, requiring research before implementation, user explicitly asks for step-by-step execution, task involves multiple distinct phases
   - **DO NOT use create_plan** when: changing a single config value, answering an informational question, reading a single file, making a one-line fix

2. **Few-shot judgment examples** (2-3 examples):
   - User: "搭建完整的用户认证系统" → create_plan (multiple files, multiple phases)
   - User: "修复这个 typo" → NO create_plan (trivial change)
   - User: "分析这个性能问题" → create_plan (requires research → implementation → verification)

3. **Self-check reminder**: "If unsure whether to create a plan, ask yourself: does the user need to see my work plan? If not, just proceed."

#### Scenario: Optimized prompt injected for solo mode
- **WHEN** an Agent runs in solo mode with plan tools available
- **THEN** the system prompt SHALL include specific boundary conditions, few-shot examples, and the self-check reminder

#### Scenario: Optimized prompt injected for coordinated mode
- **WHEN** an Agent runs in coordinated mode with plan tools available
- **THEN** the system prompt SHALL include the same optimized boundary conditions and self-check reminder
