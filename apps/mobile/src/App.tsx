import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronLeft, Home, Loader2, Menu, RefreshCw, Settings, X } from 'lucide-react'

import { createMobileApiClient } from './api/client'
import { ApprovalsScreen } from './screens/ApprovalsScreen'
import { ArtifactPreviewSheet } from './screens/ArtifactPreviewSheet'
import { ConversationsScreen } from './screens/ConversationsScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { StatusScreen } from './screens/StatusScreen'
import { usePullToRefresh } from './lib/usePullToRefresh'
import { loadConnection, loadRecentHosts, rememberRecentHost, saveConnection } from './storage/connection'
import type {
  ConnectionConfig,
  MobileAskUserAnswers,
  MobileArtifact,
  MobileConversationDetail,
  MobileSnapshot,
} from './types'

const initialConnection = loadConnection()
const initialRecentHosts = loadRecentHosts()

type AppView = 'home' | 'settings'

export function App() {
  const [activeView, setActiveView] = useState<AppView>('home')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [connection, setConnection] = useState<ConnectionConfig>(initialConnection)
  const [recentHosts, setRecentHosts] = useState<string[]>(initialRecentHosts)
  const [lastSuccessfulConnection, setLastSuccessfulConnection] = useState<string | null>(null)
  const [snapshot, setSnapshot] = useState<MobileSnapshot | null>(null)
  const [conversationDetail, setConversationDetail] = useState<MobileConversationDetail | null>(null)
  const [artifactPreview, setArtifactPreview] = useState<MobileArtifact | null>(null)
  const [artifactCache, setArtifactCache] = useState<Record<string, MobileArtifact>>({})
  const [artifactLoadingId, setArtifactLoadingId] = useState<string | null>(null)
  const [artifactError, setArtifactError] = useState<string | null>(null)
  const [selectedConversationId, setSelectedConversationId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [operationId, setOperationId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null)

  const api = useMemo(() => createMobileApiClient(connection), [connection])
  const configured = !!normalizeBaseUrl(connection.baseUrl) && !!connection.deviceToken.trim()
  const connectionFingerprint = `${normalizeBaseUrl(connection.baseUrl)}\n${connection.deviceToken.trim()}`
  const connectionOk = configured && lastSuccessfulConnection === connectionFingerprint

  const recordSnapshotSuccess = useCallback(
    (next: MobileSnapshot) => {
      setSnapshot(next)
      setLastSuccessfulConnection(connectionFingerprint)
      setRecentHosts(rememberRecentHost(connection.baseUrl))
      setLastSyncedAt(Date.now())
      setError(null)
    },
    [connection.baseUrl, connectionFingerprint],
  )

  useEffect(() => {
    saveConnection(connection)
  }, [connection])

  useEffect(() => {
    if (!configured || activeView === 'settings') return

    let cancelled = false

    async function refreshMobileSnapshot() {
      try {
        const next = await api.getSnapshot()
        if (!cancelled) recordSnapshotSuccess(next)
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }

    const timer = window.setInterval(() => {
      void refreshMobileSnapshot()
    }, 3000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [activeView, api, configured, recordSnapshotSuccess])

  useEffect(() => {
    if (!configured || !selectedConversationId) return

    let cancelled = false
    const conversationId = selectedConversationId

    async function refreshConversationDetail() {
      try {
        const next = await api.getConversation(conversationId)
        if (!cancelled) {
          setConversationDetail(next)
          setLastSuccessfulConnection(connectionFingerprint)
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      }
    }

    const timer = window.setInterval(() => {
      void refreshConversationDetail()
    }, 2000)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [api, configured, selectedConversationId, connectionFingerprint])

  async function refreshSnapshot() {
    if (!configured) {
      setError('请先在设置里填写桌面端地址和设备 token。')
      return
    }
    setLoading(true)
    setError(null)
    try {
      const next = await api.getSnapshot()
      recordSnapshotSuccess(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  async function runMobileAction(id: string, action: () => Promise<void>) {
    if (!configured) {
      setError('请先在设置里填写桌面端地址和设备 token。')
      return
    }
    setOperationId(id)
    setError(null)
    try {
      await action()
      recordSnapshotSuccess(await api.getSnapshot())
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setOperationId(null)
    }
  }

  async function openConversation(id: string) {
    if (!configured) {
      setError('请先在设置里填写桌面端地址和设备 token。')
      return
    }
    setSelectedConversationId(id)
    setActiveView('home')
    setDrawerOpen(false)
    setLoading(true)
    setError(null)
    try {
      setConversationDetail(await api.getConversation(id))
      setLastSuccessfulConnection(connectionFingerprint)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  function openHome() {
    setActiveView('home')
    setSelectedConversationId(null)
    setConversationDetail(null)
    closeArtifactPreview()
    setDrawerOpen(false)
  }

  function backToList() {
    setSelectedConversationId(null)
    setConversationDetail(null)
    closeArtifactPreview()
  }

  function openSettings() {
    setActiveView('settings')
    setSelectedConversationId(null)
    setConversationDetail(null)
    closeArtifactPreview()
    setDrawerOpen(false)
  }

  async function sendMessageFromMobile(content: string) {
    if (!selectedConversationId) return
    await runMobileAction('send-message', async () => {
      await api.sendMessage(selectedConversationId, content)
      setConversationDetail(await api.getConversation(selectedConversationId))
    })
  }

  async function withdrawMessageFromMobile(messageId: string) {
    if (!selectedConversationId) return
    await runMobileAction(`withdraw-${messageId}`, async () => {
      await api.withdrawMessage(selectedConversationId, messageId)
      setConversationDetail(await api.getConversation(selectedConversationId))
    })
  }

  async function editMessageFromMobile(messageId: string, content: string) {
    if (!selectedConversationId) return
    await runMobileAction(`edit-${messageId}`, async () => {
      await api.editMessage(selectedConversationId, messageId, content)
      setConversationDetail(await api.getConversation(selectedConversationId))
    })
  }

  async function regenerateFromMobile() {
    if (!selectedConversationId) return
    await runMobileAction('regenerate', async () => {
      await api.regenerate(selectedConversationId)
      setConversationDetail(await api.getConversation(selectedConversationId))
    })
  }

  async function openArtifactPreview(artifactId: string) {
    if (!configured) {
      setError('请先在设置里填写桌面端地址和设备 token。')
      return
    }
    const cached = artifactCache[artifactId]
    if (cached) {
      setArtifactPreview(cached)
      setArtifactError(null)
      return
    }

    setArtifactLoadingId(artifactId)
    setArtifactError(null)
    setArtifactPreview(null)
    try {
      const artifact = await api.getArtifact(artifactId)
      setArtifactCache((current) => ({ ...current, [artifact.id]: artifact }))
      setArtifactPreview(artifact)
    } catch (err) {
      setArtifactError(err instanceof Error ? err.message : String(err))
    } finally {
      setArtifactLoadingId(null)
    }
  }

  function closeArtifactPreview() {
    setArtifactPreview(null)
    setArtifactError(null)
    setArtifactLoadingId(null)
  }

  usePullToRefresh(() => {
    void refreshSnapshot()
  }, configured && !selectedConversationId && activeView === 'home')

  const hasPending = !!snapshot && (snapshot.pendingWrites.length > 0 || snapshot.pendingQuestions.length > 0)

  const content = selectedConversationId ? (
      <ConversationsScreen
        connected={configured}
        loading={loading}
        snapshot={snapshot}
        detail={conversationDetail}
        selectedConversationId={selectedConversationId}
        onOpenConversation={(id) => void openConversation(id)}
        onOpenArtifact={(id) => void openArtifactPreview(id)}
        onSendMessage={(content) => void sendMessageFromMobile(content)}
        onWithdrawMessage={(messageId) => void withdrawMessageFromMobile(messageId)}
        onEditMessage={(messageId, content) => void editMessageFromMobile(messageId, content)}
        onRegenerate={() => void regenerateFromMobile()}
        busy={operationId !== null}
      />
    ) : activeView === 'settings' ? (
      <SettingsScreen
        connection={connection}
        loading={loading}
        error={error}
        connectionOk={connectionOk}
        recentHosts={recentHosts}
        onChange={setConnection}
        onSelectHost={(baseUrl) => setConnection((current) => ({ ...current, baseUrl }))}
        onTest={() => void refreshSnapshot()}
      />
    ) : (
      <div className="screen-stack">
        <StatusScreen
          connected={connectionOk}
          error={error}
          snapshot={snapshot}
          lastSyncedAt={lastSyncedAt}
          onOpenSettings={openSettings}
          onOpenConversation={(id) => void openConversation(id)}
        />
        {hasPending && (
          <ApprovalsScreen
            connected={configured}
            busyId={operationId}
            snapshot={snapshot}
            onWriteDecision={(id, action) =>
              void runMobileAction(id, () => api.decidePendingWrite(id, action))
            }
            onQuestionAnswer={(id, answers: MobileAskUserAnswers) =>
              void runMobileAction(id, () => api.answerPendingQuestion(id, answers))
            }
          />
        )}
      </div>
    )

  const inConversation = !!selectedConversationId
  const navTitle = inConversation
    ? conversationDetail?.conversation.title ?? '会话'
    : activeView === 'settings'
      ? '设置'
      : '主页'

  let navRight: ReactNode = <span className="nav-bar-spacer" aria-hidden="true" />
  if (!inConversation && activeView === 'home') {
    if (!configured) {
      navRight = (
        <button type="button" className="nav-bar-button" aria-label="设置" onClick={openSettings}>
          <Settings className="chrome-icon" aria-hidden="true" />
        </button>
      )
    } else if (error && !loading) {
      navRight = (
        <span className="nav-bar-reconnect">
          <Loader2 className="inline-icon" aria-hidden="true" />
          重连
        </span>
      )
    } else {
      navRight = (
        <button type="button" className="nav-bar-button" aria-label="刷新" onClick={() => void refreshSnapshot()}>
          <RefreshCw className="chrome-icon" aria-hidden="true" />
        </button>
      )
    }
  }

  return (
    <main className="app-shell">
      <nav className="nav-bar">
        {inConversation ? (
          <button type="button" className="nav-bar-button" aria-label="返回会话列表" onClick={backToList}>
            <ChevronLeft className="chrome-icon" aria-hidden="true" />
          </button>
        ) : (
          <button type="button" className="nav-bar-button" aria-label="打开侧边栏" onClick={() => setDrawerOpen(true)}>
            <Menu className="chrome-icon" aria-hidden="true" />
          </button>
        )}
        <div className="nav-bar-title">{navTitle}</div>
        {navRight}
      </nav>

      <div className="screen-frame">{content}</div>

      <ArtifactPreviewSheet
        artifact={artifactPreview}
        loading={artifactLoadingId !== null}
        error={artifactError}
        onClose={closeArtifactPreview}
      />

      {drawerOpen && (
        <>
          <button type="button" className="drawer-backdrop" aria-label="关闭侧边栏" onClick={() => setDrawerOpen(false)} />
          <aside className="side-drawer" aria-label="侧边栏">
            <div className="drawer-header">
              <div>
                <p className="eyebrow">AChat</p>
                <h2>Companion</h2>
              </div>
              <button type="button" className="chrome-button" aria-label="关闭侧边栏" onClick={() => setDrawerOpen(false)}>
                <X className="chrome-icon" aria-hidden="true" />
              </button>
            </div>

            <div className="drawer-section">
              <button
                type="button"
                className={`drawer-item ${activeView === 'home' ? 'active' : ''}`}
                onClick={openHome}
              >
                <Home className="drawer-icon" aria-hidden="true" />
                主页
              </button>
              <button
                type="button"
                className={`drawer-item ${activeView === 'settings' ? 'active' : ''}`}
                onClick={openSettings}
              >
                <Settings className="drawer-icon" aria-hidden="true" />
                设置
              </button>
            </div>

            <div className="drawer-status">
              <span className={connectionOk ? 'status-pill online' : 'status-pill'}>
                {connectionOk ? '已连接' : configured ? '已配置' : '未配对'}
              </span>
              <p>
                {snapshot
                  ? `${snapshot.conversations.length} 个会话 · ${snapshot.runningRuns.length} 个运行中`
                  : '数据同步中'}
              </p>
            </div>
          </aside>
        </>
      )}
    </main>
  )
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '')
}
