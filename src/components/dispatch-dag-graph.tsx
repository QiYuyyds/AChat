'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  Position,
  Panel,
  useNodesState,
  useEdgesState,
  addEdge,
  useReactFlow,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  type EdgeProps,
} from '@xyflow/react'
import dagre from '@dagrejs/dagre'
import '@xyflow/react/dist/style.css'
import {
  AlertTriangle,
  Ban,
  Bell,
  Check,
  CheckCircle2,
  Circle,
  GitBranch,
  Loader2,
  Maximize2,
  Minimize2,
  Plus,
  RotateCw,
  Trash2,
  X,
  XCircle,
} from 'lucide-react'
import { AgentAvatar } from '@/components/agent-avatar'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { DispatchState } from '@/stores/app-store'
import type { AgentRow } from '@/db/schema'
import type { DispatchPlanItem, DispatchTaskStatus } from '@/shared/types'

const NODE_WIDTH = 280
const NODE_HEIGHT = 120

// ─── Types ────────────────────────────────────────────────────────

// React Flow v12 requires data types to extend Record<string, unknown>
interface TaskNodeData extends Record<string, unknown> {
  task: DispatchPlanItem
  status: DispatchTaskStatus
  agent?: { id: string; name: string; avatar: string }
  worktree?: { branchName: string; path: string; mergeStatus?: 'success' | 'conflict' }
  retryInfo?: { attempt: number; maxAttempts: number; error?: string }
  editable: boolean
  selected?: boolean
  pendingApprovalCount?: number
}

interface EdgeData extends Record<string, unknown> {
  editable: boolean
  onDelete?: (edgeId: string) => void
  sourceStatus?: DispatchTaskStatus
  targetStatus?: DispatchTaskStatus
}

interface DispatchDAGGraphProps {
  dispatch: DispatchState
  editable?: boolean
  onPlanChange?: (plan: DispatchPlanItem[]) => void
  agents: Record<string, AgentRow>
  validationErrors?: string[]
  selectedTaskId?: string | null
  onTaskSelect?: (taskId: string) => void
  pendingApprovals?: Record<string, number>
}

// ─── Node / Edge type registrations ────────────────────────────────

const nodeTypes = { task: TaskNode }
const edgeTypes = { editable: EditableEdge }

// ─── Conversion helpers ────────────────────────────────────────────

function planToNodes(
  plan: DispatchPlanItem[],
  dispatch: DispatchState,
  agents: Record<string, AgentRow>,
  editable: boolean,
  selectedTaskId?: string | null,
): Node[] {
  return plan.map((task) => {
    const status: DispatchTaskStatus =
      dispatch.reviewStatus === 'rejected'
        ? 'skipped'
        : (dispatch.taskStatus[task.id] ?? 'pending')
    const agent = agents[task.agentId]
    return {
      id: task.id,
      type: 'task',
      position: { x: 0, y: 0 },
      data: {
        task,
        status,
        agent: agent
          ? { id: agent.id, name: agent.name, avatar: agent.avatar }
          : undefined,
        worktree: dispatch.worktreeByTask?.[task.id],
        retryInfo: dispatch.retryInfo?.[task.id],
        editable,
        selected: selectedTaskId === task.id,
      } as TaskNodeData,
    }
  })
}

function planToEdges(
  plan: DispatchPlanItem[],
  dispatch: DispatchState,
  editable: boolean,
  onDelete?: (edgeId: string) => void,
): Edge[] {
  const edges: Edge[] = []
  for (const task of plan) {
    const deps = task.dependsOn ?? []
    for (const dep of deps) {
      const targetRunning = dispatch.taskStatus[task.id] === 'running'
      edges.push({
        id: `e_${dep}_${task.id}`,
        source: dep,
        target: task.id,
        type: 'editable',
        animated: targetRunning,
        data: { editable, onDelete } as EdgeData,
      })
    }
  }
  return edges
}

function layoutWithDagre(nodes: Node[], edges: Edge[]): Node[] {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'TB', nodesep: 60, ranksep: 70 })
  g.setDefaultEdgeLabel(() => ({}))

  for (const node of nodes) {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT })
  }
  for (const edge of edges) {
    g.setEdge(edge.source, edge.target)
  }

  dagre.layout(g)

  return nodes.map((node) => {
    const pos = g.node(node.id)
    return {
      ...node,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    }
  })
}

function planFromNodesEdges(nodes: Node[], edges: Edge[]): DispatchPlanItem[] {
  return nodes.map((node) => {
    const data = node.data as TaskNodeData
    return {
      ...data.task,
      dependsOn: edges
        .filter((e) => e.target === node.id)
        .map((e) => e.source),
    }
  })
}

// ─── Small UI helpers ──────────────────────────────────────────────

function statusBorder(status: DispatchTaskStatus): string {
  switch (status) {
    case 'running':
      return 'border-warning/50 bg-warning/5'
    case 'complete':
      return 'border-emerald-300 dark:border-emerald-900/40 bg-emerald-50/30 dark:bg-emerald-950/10'
    case 'failed':
      return 'border-destructive/40 bg-destructive/5'
    case 'merge_conflict':
      return 'border-warning/40 bg-warning/5'
    case 'aborted':
      return 'border-zinc-300 bg-zinc-50/50 dark:border-zinc-700'
    case 'skipped':
      return 'border-zinc-200 bg-muted/40 dark:border-zinc-800'
    default:
      return 'border-border bg-card'
  }
}

function StatusIcon({ status }: { status: DispatchTaskStatus }) {
  const base = 'size-3.5 shrink-0'
  if (status === 'pending') return <Circle className={cn(base, 'text-muted-foreground/40')} />
  if (status === 'running') return <Loader2 className={cn(base, 'animate-spin text-warning')} />
  if (status === 'complete') return <CheckCircle2 className={cn(base, 'text-success')} />
  if (status === 'merge_conflict') return <AlertTriangle className={cn(base, 'text-warning')} />
  if (status === 'aborted' || status === 'skipped') return <Ban className={cn(base, 'text-zinc-500')} />
  return <XCircle className={cn(base, 'text-destructive')} />
}

function WorktreeBadge({
  worktree,
}: {
  worktree?: { branchName: string; path: string; mergeStatus?: 'success' | 'conflict' }
}) {
  if (!worktree) return null
  const isConflict = worktree.mergeStatus === 'conflict'
  const isMerged = worktree.mergeStatus === 'success'
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 rounded-full px-1.5 py-0.5 text-[10px] font-medium',
        isConflict
          ? 'bg-warning/15 text-warning'
          : isMerged
            ? 'bg-success/15 text-success'
            : 'bg-primary/10 text-primary',
      )}
      title={worktree.path}
    >
      <GitBranch className="size-2.5" />
      {worktree.branchName}
      {isMerged && <Check className="size-2.5" />}
      {isConflict && <AlertTriangle className="size-2.5" />}
    </span>
  )
}

function RetryBadge({
  info,
}: {
  info: { attempt: number; maxAttempts: number; error?: string }
}) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-1.5 py-0.5 text-[10px] font-medium text-warning"
      title={info.error ?? undefined}
    >
      <RotateCw className="size-2.5 animate-spin" />
      {info.attempt}/{info.maxAttempts}
    </span>
  )
}

// ─── TaskNode component ────────────────────────────────────────────

function TaskNode({ data }: NodeProps) {
  const d = data as TaskNodeData
  const isRunning = d.status === 'running'
  const isComplete = d.status === 'complete'
  const isPending = d.status === 'pending' && !d.editable
  const hasPending = (d.pendingApprovalCount ?? 0) > 0
  return (
    <div
      className={cn(
        'relative rounded-md border p-2 shadow-sm transition-colors',
        statusBorder(d.status),
        isRunning && 'dag-node-running',
        isComplete && 'dag-node-complete',
        isPending && 'dag-node-pending',
        d.selected && 'ring-2 ring-primary',
      )}
      style={{ width: NODE_WIDTH }}
    >
      {isRunning && <div className="dag-shimmer-track" />}
      {hasPending && (
        <div className="dag-approval-badge">
          <Bell className="size-3" />
          {d.pendingApprovalCount! > 1 && (
            <span className="dag-approval-count">{d.pendingApprovalCount}</span>
          )}
        </div>
      )}
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={d.editable}
        className={cn('!bg-muted-foreground/50', !d.editable && '!opacity-0')}
      />
      <div className="relative flex items-center gap-1.5">
        <StatusIcon status={d.status} />
        {d.agent ? (
          <AgentAvatar agent={d.agent} size="xs" />
        ) : (
          <div className="size-5 shrink-0 rounded-full bg-muted" />
        )}
        <span className="font-mono text-[10px] text-muted-foreground">{d.task.id}</span>
        <WorktreeBadge worktree={d.worktree} />
        {d.retryInfo && <RetryBadge info={d.retryInfo} />}
      </div>
      <div className="relative mt-1 line-clamp-2 text-xs text-muted-foreground">
        {d.task.task}
      </div>
      {isRunning && (
        <div className="absolute bottom-0 left-0 right-0 h-0.5 overflow-hidden rounded-b-md">
          <div className="dag-progress-bar h-full" />
        </div>
      )}
      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={d.editable}
        className={cn('!bg-muted-foreground/50', !d.editable && '!opacity-0')}
      />
    </div>
  )
}

// ─── EditableEdge component ────────────────────────────────────────

function EditableEdge(props: EdgeProps) {
  const {
    id,
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    data,
  } = props
  const d = data as EdgeData | undefined
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })

  const targetRunning = d?.targetStatus === 'running'
  const flowComplete = d?.sourceStatus === 'complete' && d?.targetStatus === 'complete'

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={flowComplete ? { stroke: 'var(--success)', strokeWidth: 1.5 } : undefined}
      />
      {targetRunning && (
        <path
          d={edgePath}
          fill="none"
          stroke="var(--warning)"
          strokeWidth={2}
          className="dag-edge-flowing"
        />
      )}
      {d?.editable && (
        <EdgeLabelRenderer>
          <button
            type="button"
            className="flex size-4 items-center justify-center rounded-full border border-border bg-background text-muted-foreground shadow-sm transition-colors hover:bg-destructive hover:text-destructive-foreground"
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
            }}
            onClick={() => d.onDelete?.(id)}
          >
            <X className="size-2.5" />
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

// ─── Add Node Form ─────────────────────────────────────────────────

function AddNodeForm({
  agents,
  existingIds,
  onSubmit,
  onCancel,
}: {
  agents: Record<string, AgentRow>
  existingIds: string[]
  onSubmit: (task: DispatchPlanItem) => void
  onCancel: () => void
}) {
  const [taskId, setTaskId] = useState('')
  const [agentId, setAgentId] = useState(Object.keys(agents)[0] ?? '')
  const [task, setTask] = useState('')
  const [deps, setDeps] = useState<string[]>([])

  const toggleDep = (id: string) => {
    setDeps((prev) => (prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]))
  }

  const handleSubmit = () => {
    if (!taskId.trim() || !agentId || !task.trim()) return
    onSubmit({
      id: taskId.trim(),
      agentId,
      task: task.trim(),
      dependsOn: deps.length > 0 ? deps : undefined,
    })
  }

  const agentList = Object.values(agents)

  return (
    <div className="space-y-2 rounded-lg border bg-card p-3 shadow-lg" style={{ width: 320 }}>
      <div className="text-sm font-medium">添加任务节点</div>
      <div className="space-y-1.5">
        <Input
          placeholder="Task ID (如 t4)"
          value={taskId}
          onChange={(e) => setTaskId(e.target.value)}
          className="h-8 text-xs"
        />
        <select
          className="h-8 w-full rounded-md border bg-background px-2 text-xs"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        >
          {agentList.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <Textarea
          placeholder="任务描述"
          value={task}
          onChange={(e) => setTask(e.target.value)}
          className="min-h-[60px] text-xs"
        />
        {existingIds.length > 0 && (
          <div className="space-y-1">
            <div className="text-[10px] text-muted-foreground">依赖任务</div>
            <div className="flex flex-wrap gap-1">
              {existingIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => toggleDep(id)}
                  className={cn(
                    'rounded border px-1.5 py-0.5 font-mono text-[10px] transition-colors',
                    deps.includes(id)
                      ? 'border-primary bg-primary/10 text-primary'
                      : 'border-border text-muted-foreground hover:bg-muted',
                  )}
                >
                  {id}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 text-xs">
          取消
        </Button>
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={!taskId.trim() || !task.trim()}
          className="h-7 text-xs"
        >
          添加
        </Button>
      </div>
    </div>
  )
}

// ─── Edit Node Form ────────────────────────────────────────────────

function EditNodeForm({
  task,
  agents,
  onSubmit,
  onCancel,
}: {
  task: DispatchPlanItem
  agents: Record<string, AgentRow>
  onSubmit: (task: DispatchPlanItem) => void
  onCancel: () => void
}) {
  const [taskDesc, setTaskDesc] = useState(task.task)
  const [agentId, setAgentId] = useState(task.agentId)

  const handleSubmit = () => {
    if (!taskDesc.trim() || !agentId) return
    onSubmit({ ...task, task: taskDesc.trim(), agentId })
  }

  const agentList = Object.values(agents)

  return (
    <div className="space-y-2 rounded-lg border bg-card p-3 shadow-lg" style={{ width: 320 }}>
      <div className="text-sm font-medium">编辑任务 <span className="font-mono text-[10px] text-muted-foreground">{task.id}</span></div>
      <div className="space-y-1.5">
        <select
          className="h-8 w-full rounded-md border bg-background px-2 text-xs"
          value={agentId}
          onChange={(e) => setAgentId(e.target.value)}
        >
          {agentList.map((a) => (
            <option key={a.id} value={a.id}>{a.name}</option>
          ))}
        </select>
        <Textarea
          placeholder="任务描述"
          value={taskDesc}
          onChange={(e) => setTaskDesc(e.target.value)}
          className="min-h-[60px] text-xs"
        />
      </div>
      <div className="flex justify-end gap-1.5">
        <Button size="sm" variant="ghost" onClick={onCancel} className="h-7 text-xs">
          取消
        </Button>
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={!taskDesc.trim() || !agentId}
          className="h-7 text-xs"
        >
          保存
        </Button>
      </div>
    </div>
  )
}

// ─── Inner component (uses useReactFlow) ───────────────────────────

function DAGGraphInner({
  dispatch,
  editable = false,
  onPlanChange,
  agents,
  validationErrors,
  selectedTaskId,
  onTaskSelect,
  pendingApprovals,
}: DispatchDAGGraphProps) {
  const { screenToFlowPosition, fitView } = useReactFlow()
  const [showAddForm, setShowAddForm] = useState(false)
  const [addFormPos, setAddFormPos] = useState({ x: 0, y: 0 })
  const [editingTask, setEditingTask] = useState<DispatchPlanItem | null>(null)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; nodeId: string } | null>(null)
  const [expanded, setExpanded] = useState(false)

  // Compute initial nodes/edges from plan
  const initialNodes = useMemo(() => {
    const raw = planToNodes(dispatch.plan, dispatch, agents, editable, selectedTaskId)
    const rawEdges = planToEdges(dispatch.plan, dispatch, editable)
    return layoutWithDagre(raw, rawEdges)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps -- init only

  const initialEdges = useMemo(
    () => planToEdges(dispatch.plan, dispatch, editable),
    [], // eslint-disable-line react-hooks/exhaustive-deps -- init only
  )

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)

  const handleDeleteEdge = useCallback(
    (edgeId: string) => {
      const edge = edges.find((e) => e.id === edgeId)
      if (!edge) return
      const newEdges = edges.filter((e) => e.id !== edgeId)
      setEdges(newEdges)
      const updatedPlan = planFromNodesEdges(nodes, newEdges)
      onPlanChange?.(updatedPlan)
    },
    [edges, nodes, setEdges, onPlanChange],
  )

  // Update edge data with delete callback
  const edgesWithCallback = useMemo(
    () =>
      edges.map((e) => {
        const sourceStatus = dispatch.taskStatus[e.source] ?? 'pending'
        const targetStatus = dispatch.taskStatus[e.target] ?? 'pending'
        return {
          ...e,
          animated: targetStatus === 'running',
          data: {
            ...(e.data as EdgeData),
            onDelete: handleDeleteEdge,
            sourceStatus,
            targetStatus,
          } as EdgeData,
        }
      }),
    [edges, handleDeleteEdge, dispatch.taskStatus],
  )

  const onConnect = useCallback(
    (connection: Connection) => {
      if (!connection.source || !connection.target) return
      if (edges.some((e) => e.source === connection.source && e.target === connection.target)) {
        return
      }
      const newEdge: Edge = {
        ...connection,
        id: `e_${connection.source}_${connection.target}`,
        type: 'editable',
        animated: false,
        data: { editable, onDelete: handleDeleteEdge } as EdgeData,
      }
      const newEdges = addEdge(newEdge, edges)
      setEdges(newEdges)
      const updatedPlan = planFromNodesEdges(nodes, newEdges)
      onPlanChange?.(updatedPlan)
    },
    [edges, nodes, setEdges, editable, handleDeleteEdge, onPlanChange],
  )

  const handleOpenAddForm = useCallback(() => {
    if (!editable) return
    const rect = document.querySelector('.react-flow')?.getBoundingClientRect()
    const x = rect ? rect.left + rect.width / 2 : window.innerWidth / 2
    const y = rect ? rect.top + rect.height / 2 : window.innerHeight / 2
    const position = screenToFlowPosition({ x, y })
    setAddFormPos(position)
    setShowAddForm(true)
  }, [editable, screenToFlowPosition])

  const handleAddNode = useCallback(
    (task: DispatchPlanItem) => {
      const agent = agents[task.agentId]
      const newNode: Node = {
        id: task.id,
        type: 'task',
        position: addFormPos,
        data: {
          task,
          status: 'pending' as DispatchTaskStatus,
          agent: agent
            ? { id: agent.id, name: agent.name, avatar: agent.avatar }
            : undefined,
          editable,
        } as TaskNodeData,
      }
      const newNodes = [...nodes, newNode]
      setNodes(newNodes)
      if (task.dependsOn) {
        const newDeps: Edge[] = task.dependsOn.map((dep) => ({
          id: `e_${dep}_${task.id}`,
          source: dep,
          target: task.id,
          type: 'editable',
          animated: false,
          data: { editable, onDelete: handleDeleteEdge } as EdgeData,
        }))
        const newEdges = [...edges, ...newDeps]
        setEdges(newEdges)
        const updatedPlan = planFromNodesEdges(newNodes, newEdges)
        onPlanChange?.(updatedPlan)
      } else {
        const updatedPlan = planFromNodesEdges(newNodes, edges)
        onPlanChange?.(updatedPlan)
      }
      setShowAddForm(false)
    },
    [nodes, edges, setNodes, setEdges, agents, editable, addFormPos, handleDeleteEdge, onPlanChange],
  )

  const handleDeleteNode = useCallback(
    (taskId: string) => {
      const newNodes = nodes.filter((n) => n.id !== taskId)
      const newEdges = edges.filter((e) => e.source !== taskId && e.target !== taskId)
      setNodes(newNodes)
      setEdges(newEdges)
      const updatedPlan = planFromNodesEdges(newNodes, newEdges)
      onPlanChange?.(updatedPlan)
    },
    [nodes, edges, setNodes, setEdges, onPlanChange],
  )

  const handleEditNode = useCallback(
    (task: DispatchPlanItem) => {
      const agent = agents[task.agentId]
      setNodes((nds) =>
        nds.map((n) =>
          n.id === task.id
            ? {
                ...n,
                data: {
                  ...(n.data as TaskNodeData),
                  task,
                  agent: agent
                    ? { id: agent.id, name: agent.name, avatar: agent.avatar }
                    : undefined,
                } as TaskNodeData,
              }
            : n,
        ),
      )
      const updatedNodes = nodes.map((n) =>
        n.id === task.id
          ? { ...n, data: { ...(n.data as TaskNodeData), task } as TaskNodeData }
          : n,
      )
      const updatedPlan = planFromNodesEdges(updatedNodes, edges)
      onPlanChange?.(updatedPlan)
      setEditingTask(null)
    },
    [nodes, edges, setNodes, agents, onPlanChange],
  )

  const onNodeDoubleClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (!editable) return
      const data = node.data as TaskNodeData
      setEditingTask(data.task)
    },
    [editable],
  )

  const onNodeContextMenu = useCallback(
    (event: React.MouseEvent, node: Node) => {
      if (!editable) return
      event.preventDefault()
      setContextMenu({ x: event.clientX, y: event.clientY, nodeId: node.id })
    },
    [editable],
  )

  const onPaneClick = useCallback(() => {
    setContextMenu(null)
    setShowAddForm(false)
    setEditingTask(null)
  }, [])

  // Read-only mode: re-layout when dispatch.plan changes (e.g. after approval
  // the backend sends dispatch.plan with the modified plan).
  // Editable mode skips this to preserve user edits.
  const planSignature = useMemo(
    () => dispatch.plan.map((t) => `${t.id}:${(t.dependsOn ?? []).join('-')}`).join(','),
    [dispatch.plan],
  )
  useEffect(() => {
    if (editable) return
    const raw = planToNodes(dispatch.plan, dispatch, agents, editable, selectedTaskId)
    const rawEdges = planToEdges(dispatch.plan, dispatch, editable)
    setNodes(layoutWithDagre(raw, rawEdges))
    setEdges(rawEdges)
    const timer = setTimeout(() => fitView({ padding: 0.1, duration: 200 }), 50)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- planSignature captures plan structure changes
  }, [planSignature])

  // Update node `selected` state when selectedTaskId changes (without full re-layout)
  useEffect(() => {
    setNodes((nds) =>
      nds.map((n) => {
        const data = n.data as TaskNodeData
        return { ...n, data: { ...data, selected: selectedTaskId === n.id } as TaskNodeData }
      }),
    )
  }, [selectedTaskId, setNodes])

  // Sync dispatch taskStatus → node data (without full re-layout)
  // planSignature only captures plan structure changes, not status changes.
  // Without this, node status stays at initial value and animations never trigger.
  useEffect(() => {
    if (editable) return
    setNodes((nds) =>
      nds.map((n) => {
        const data = n.data as TaskNodeData
        const newStatus: DispatchTaskStatus =
          dispatch.reviewStatus === 'rejected'
            ? 'skipped'
            : (dispatch.taskStatus[n.id] ?? 'pending')
        const newWorktree = dispatch.worktreeByTask?.[n.id]
        const newRetryInfo = dispatch.retryInfo?.[n.id]
        const newPendingCount = pendingApprovals?.[n.id] ?? 0
        if (
          data.status === newStatus &&
          data.worktree === newWorktree &&
          data.retryInfo === newRetryInfo &&
          (data.pendingApprovalCount ?? 0) === newPendingCount
        )
          return n
        return {
          ...n,
          data: {
            ...data,
            status: newStatus,
            worktree: newWorktree,
            retryInfo: newRetryInfo,
            pendingApprovalCount: newPendingCount,
          } as TaskNodeData,
        }
      }),
    )
  }, [dispatch.taskStatus, dispatch.worktreeByTask, dispatch.retryInfo, dispatch.reviewStatus, pendingApprovals, editable, setNodes])

  const onNodeClick = useCallback(
    (_event: React.MouseEvent, node: Node) => {
      if (editable) return
      onTaskSelect?.(node.id)
    },
    [editable, onTaskSelect],
  )

  useEffect(() => {
    const timer = setTimeout(() => fitView({ padding: 0.1, duration: 200 }), 200)
    return () => clearTimeout(timer)
  }, [expanded, fitView])

  return (
    <>
      {expanded && (
        <div
          className="fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
          onClick={() => setExpanded(false)}
        />
      )}
      <div
        className={cn(
          'relative w-full overflow-hidden rounded-lg border bg-muted/20 transition-all duration-200',
          expanded
            ? 'fixed inset-6 z-50 h-[85vh] shadow-2xl'
            : editable
              ? 'h-[480px]'
              : dispatch.plan.length <= 3
                ? 'h-[400px]'
                : 'h-[520px]',
        )}
      >
        <ReactFlow
          nodes={nodes}
          edges={edgesWithCallback}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeDoubleClick={onNodeDoubleClick}
          onNodeClick={onNodeClick}
          onNodeContextMenu={onNodeContextMenu}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          nodesDraggable={editable}
          nodesConnectable={editable}
          elementsSelectable={editable}
          deleteKeyCode={editable ? ['Backspace', 'Delete'] : []}
          fitView
          fitViewOptions={{ padding: 0.1 }}
          minZoom={0.5}
          proOptions={{ hideAttribution: true }}
        >
        <Background variant={BackgroundVariant.Dots} gap={16} className="!bg-muted/30" />
        <Controls className="!bg-background !border !shadow-sm" showInteractive={editable} />
      </ReactFlow>

      {showAddForm && (
        <Panel position="top-center" className="!pointer-events-auto">
          <AddNodeForm
            agents={agents}
            existingIds={nodes.map((n) => n.id)}
            onSubmit={handleAddNode}
            onCancel={() => setShowAddForm(false)}
          />
        </Panel>
      )}

      {editingTask && (
        <Panel position="top-center" className="!pointer-events-auto">
          <EditNodeForm
            task={editingTask}
            agents={agents}
            onSubmit={handleEditNode}
            onCancel={() => setEditingTask(null)}
          />
        </Panel>
      )}

      {contextMenu && (
        <div
          className="fixed z-50 rounded-md border bg-background p-1 shadow-lg"
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button
            type="button"
            className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-destructive transition-colors hover:bg-destructive/10"
            onClick={() => {
              handleDeleteNode(contextMenu.nodeId)
              setContextMenu(null)
            }}
          >
            <Trash2 className="size-3" />
            删除节点
          </button>
        </div>
      )}

      {validationErrors && validationErrors.length > 0 && (
        <Panel position="bottom-center">
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-1.5 text-xs text-destructive">
            {validationErrors[0]}
          </div>
        </Panel>
      )}

      <Panel position="top-right">
        <div className="flex items-center gap-1">
          {editable && !showAddForm && !editingTask && (
            <button
              type="button"
              onClick={handleOpenAddForm}
              className="flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-[10px] text-primary shadow-sm transition-colors hover:bg-primary/10"
            >
              <Plus className="size-3" />
              添加节点
            </button>
          )}
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1 rounded-md border bg-background px-2 py-1 text-[10px] text-muted-foreground shadow-sm transition-colors hover:bg-accent"
          >
            {expanded ? <Minimize2 className="size-3" /> : <Maximize2 className="size-3" />}
            {expanded ? '收起' : '展开'}
          </button>
        </div>
      </Panel>
    </div>
    </>
  )
}

// ─── Main export (wraps with ReactFlowProvider) ────────────────────

export function DispatchDAGGraph(props: DispatchDAGGraphProps) {
  return (
    <ReactFlowProvider>
      <DAGGraphInner {...props} />
    </ReactFlowProvider>
  )
}
