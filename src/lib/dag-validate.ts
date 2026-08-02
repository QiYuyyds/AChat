/**
 * Frontend mirror of backend/app/services/dag_executor.py:validate_dag.
 *
 * Mirrors the four validation checks: duplicate ids, self-dependencies,
 * missing references, and cycle detection (via topological wave sort).
 * If the backend validate_dag logic changes, update this file too.
 */
import type { DispatchPlanItem } from '@/shared/types'

export function validateDagFrontend(plan: DispatchPlanItem[]): string[] {
  const errors: string[] = []

  // 1. Duplicate task ids
  const seenIds = new Set<string>()
  for (const t of plan) {
    if (seenIds.has(t.id)) {
      errors.push(`Duplicate task id: '${t.id}'`)
    }
    seenIds.add(t.id)
  }

  // 2. Self-dependencies
  for (const t of plan) {
    const deps = t.dependsOn ?? []
    if (deps.includes(t.id)) {
      errors.push(`Task '${t.id}' depends on itself`)
    }
  }

  // 3. Missing references
  for (const t of plan) {
    const deps = t.dependsOn ?? []
    for (const dep of deps) {
      if (!seenIds.has(dep)) {
        errors.push(`Task '${t.id}' depends on unknown task '${dep}'`)
      }
    }
  }

  // 4. Cycle detection (mirror topological_waves)
  if (errors.length === 0) {
    const cycleError = detectCycle(plan)
    if (cycleError) {
      errors.push(cycleError)
    }
  }

  return errors
}

function detectCycle(plan: DispatchPlanItem[]): string | null {
  const completed = new Set<string>()
  const remaining = [...plan]

  while (remaining.length > 0) {
    const ready = remaining.filter((t) =>
      (t.dependsOn ?? []).every((d) => completed.has(d)),
    )

    if (ready.length === 0) {
      const remainingIds = remaining.map((t) => t.id).join(', ')
      return `Cycle detected in task dependencies (involves: ${remainingIds})`
    }

    for (const t of ready) {
      completed.add(t.id)
      const idx = remaining.indexOf(t)
      if (idx !== -1) remaining.splice(idx, 1)
    }
  }

  return null
}
