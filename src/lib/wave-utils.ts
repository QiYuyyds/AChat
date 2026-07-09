import type { DispatchPlanItem } from '@/shared/types'
import type { MessageRow } from '@/db/schema'

export interface ChildRunWaveInfo {
  wave: number
  taskId: string
  orchestratorRunId: string
  agentId: string
  planIndex: number
}

export type WaveColumn = {
  taskId: string
  agentId: string
  childRunId: string
  messages: MessageRow[]
}

export type Segment =
  | { kind: 'single'; messages: MessageRow[] }
  | {
      kind: 'wave'
      orchestratorRunId: string
      wave: number
      columns: WaveColumn[]
    }

export function computeWaves(plan: DispatchPlanItem[]): Record<string, number> {
  const waveOf: Record<string, number> = {}
  const resolved = new Set<string>()

  const resolve = (itemId: string): number => {
    if (resolved.has(itemId)) return waveOf[itemId] ?? 0
    resolved.add(itemId)

    const item = plan.find((p) => p.id === itemId)
    if (!item || !item.dependsOn || item.dependsOn.length === 0) {
      waveOf[itemId] = 0
      return 0
    }
    const maxDepWave = Math.max(...item.dependsOn.map((d) => resolve(d)))
    waveOf[itemId] = maxDepWave + 1
    return waveOf[itemId]
  }

  for (const item of plan) {
    resolve(item.id)
  }

  return waveOf
}

function buildWaveColumns(
  orchestratorRunId: string,
  wave: number,
  childRunWaveMap: Record<string, ChildRunWaveInfo>,
): WaveColumn[] {
  return Object.entries(childRunWaveMap)
    .filter(([, info]) => info.orchestratorRunId === orchestratorRunId && info.wave === wave)
    .sort(([, a], [, b]) => a.planIndex - b.planIndex)
    .map(([childRunId, info]) => ({
      taskId: info.taskId,
      agentId: info.agentId,
      childRunId,
      messages: [],
    }))
}

export function buildSegments(
  messages: MessageRow[],
  childRunWaveMap: Record<string, ChildRunWaveInfo>,
): Segment[] {
  const segments: Segment[] = []
  let currentSingle: MessageRow[] = []
  let currentWave: Extract<Segment, { kind: 'wave' }> | null = null

  const flushSingle = () => {
    if (currentSingle.length > 0) {
      segments.push({ kind: 'single', messages: currentSingle })
      currentSingle = []
    }
  }

  const flushWave = () => {
    if (currentWave) {
      segments.push(currentWave)
      currentWave = null
    }
  }

  for (const msg of messages) {
    const waveInfo = msg.runId ? childRunWaveMap[msg.runId] : undefined

    if (!waveInfo) {
      flushWave()
      currentSingle.push(msg)
    } else if (
      currentWave &&
      currentWave.orchestratorRunId === waveInfo.orchestratorRunId &&
      currentWave.wave === waveInfo.wave
    ) {
      const col = currentWave.columns.find((c) => c.taskId === waveInfo.taskId)
      if (col) {
        col.messages.push(msg)
      } else {
        currentWave.columns.push({
          taskId: waveInfo.taskId,
          agentId: waveInfo.agentId,
          childRunId: msg.runId!,
          messages: [msg],
        })
      }
    } else {
      flushWave()
      flushSingle()

      const columns = buildWaveColumns(
        waveInfo.orchestratorRunId,
        waveInfo.wave,
        childRunWaveMap,
      )
      const col = columns.find((c) => c.taskId === waveInfo.taskId)
      if (col) {
        col.messages.push(msg)
      }
      currentWave = {
        kind: 'wave',
        orchestratorRunId: waveInfo.orchestratorRunId,
        wave: waveInfo.wave,
        columns,
      }
    }
  }

  flushWave()
  flushSingle()

  return segments
}
