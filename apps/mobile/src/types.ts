export interface ConnectionConfig {
  baseUrl: string
  deviceToken: string
}

export interface MobileConversationSummary {
  id: string
  title: string
  mode: 'single' | 'group'
  updatedAt: number
  runningRunCount: number
  pendingWriteCount: number
  pendingQuestionCount: number
}

export interface MobileAgent {
  id: string
  name: string
  avatar: string
  description: string
  isOrchestrator: boolean
}

export interface MobileRun {
  id: string
  conversationId: string
  agentId: string
  status: 'queued' | 'running' | 'complete' | 'failed' | 'aborted'
  startedAt: number
}

export type MobileMessagePart =
  | { type: 'text'; content: string }
  | { type: 'code'; language: string; content: string }
  | { type: 'thinking'; content: string }
  | { type: 'tool_use'; callId: string; toolName: string }
  | { type: 'tool_result'; callId: string; isError: boolean }
  | { type: 'artifact_ref'; artifactId: string }
  | {
      type: 'deploy_status'
      title: string
      version: number
      sourceType?: 'artifact' | 'workspace'
      workspacePath?: string
      previewPath: string
      status: 'ready' | 'failed'
      error?: string
    }
  | { type: 'attachment'; fileName: string; kind: 'image' | 'file' }

export type MobileArtifactType = 'web_app' | 'code_file' | 'diff' | 'document' | 'image'

export type MobileArtifactContent =
  | {
      type: 'web_app'
      files: Record<string, string>
      entry: string
    }
  | {
      type: 'code_file'
      workspacePath: string
      language: string
      sizeBytes: number
      checksum: string
    }
  | {
      type: 'diff'
      targetArtifactId: string
      hunks: Array<{
        oldStart: number
        oldLines: number
        newStart: number
        newLines: number
        lines: string[]
      }>
      applied: boolean
    }
  | {
      type: 'document'
      format: 'markdown'
      content: string
    }
  | {
      type: 'image'
      url: string
      alt: string
      width?: number
      height?: number
    }

export interface MobileArtifactSummary {
  id: string
  type: MobileArtifactType
  title: string
  version: number
  createdAt: number
}

export interface MobileArtifact extends MobileArtifactSummary {
  conversationId: string
  content: MobileArtifactContent
  parentArtifactId?: string
  createdByAgentId: string
}

export interface MobileMessage {
  id: string
  role: 'user' | 'agent' | 'system'
  agentId: string | null
  parts: MobileMessagePart[]
  status: 'streaming' | 'complete' | 'error' | 'aborted'
  createdAt: number
}

export interface MobileConversationDetail {
  conversation: {
    id: string
    title: string
    mode: 'single' | 'group'
    agentIds: string[]
    updatedAt: number
  }
  messages: MobileMessage[]
  artifacts: MobileArtifactSummary[]
  runningRuns: MobileRun[]
  pendingWrites: MobilePendingWrite[]
  pendingQuestions: MobilePendingQuestion[]
}

export interface MobilePendingWrite {
  id: string
  conversationId: string
  agentId: string
  runId: string
  path: string
  oldContent: string | null
  newContent: string
  createdAt: number
}

export interface MobilePendingQuestion {
  id: string
  conversationId: string
  agentId: string
  runId: string
  questions: Array<{
    question: string
    header: string
    options: Array<{ label: string; description?: string }>
    multiSelect?: boolean
  }>
  createdAt: number
}

export interface MobileAskUserAnswer {
  selectedLabels: string[]
  freeformNote?: string
}

export type MobileAskUserAnswers = Record<string, MobileAskUserAnswer>

export interface MobileSnapshot {
  conversations: MobileConversationSummary[]
  agents: MobileAgent[]
  runningRuns: MobileRun[]
  pendingWrites: MobilePendingWrite[]
  pendingQuestions: MobilePendingQuestion[]
  server: {
    version: string
    companionMode: 'lan' | 'tailnet'
  }
}
