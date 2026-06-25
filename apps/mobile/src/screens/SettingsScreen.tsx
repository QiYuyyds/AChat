import { CheckCircle2, Clock, Link, Wifi } from 'lucide-react'

import type { ConnectionConfig } from '../types'

export function SettingsScreen({
  connection,
  loading,
  error,
  connectionOk,
  recentHosts,
  onChange,
  onTest,
  onSelectHost,
}: {
  connection: ConnectionConfig
  loading: boolean
  error: string | null
  connectionOk: boolean
  recentHosts: string[]
  onChange: (next: ConnectionConfig) => void
  onTest: () => void
  onSelectHost: (host: string) => void
}) {
  const usingLoopback = isLoopbackUrl(connection.baseUrl)

  return (
    <div className="screen-stack">
      <section className="panel vertical">
        <div className="settings-heading">
          <h2>桌面端连接</h2>
          <span className={connectionOk ? 'status-pill online' : 'status-pill'}>
            {connectionOk ? '桌面端已连接' : '请连接桌面端'}
          </span>
        </div>

        <label className="field">
          <span>桌面端地址</span>
          <input
            value={connection.baseUrl}
            placeholder="http://100.x.y.z:3000"
            inputMode="url"
            autoCapitalize="none"
            onChange={(e) => onChange({ ...connection, baseUrl: e.target.value })}
          />
        </label>

        {usingLoopback && (
          <div className="warning-banner">
            <Link className="inline-icon" aria-hidden="true" />
            真机请填写电脑的局域网或 Tailscale 地址
          </div>
        )}

        {recentHosts.length > 0 && (
          <div className="recent-hosts">
            <div className="recent-hosts-title">
              <Clock className="inline-icon" aria-hidden="true" />
              最近地址
            </div>
            <div className="recent-hosts-list">
              {recentHosts.map((host) => (
                <button
                  key={host}
                  type="button"
                  className="recent-host-button"
                  onClick={() => onSelectHost(host)}
                >
                  {host}
                </button>
              ))}
            </div>
          </div>
        )}

        <label className="field">
          <span>设备 token</span>
          <input
            value={connection.deviceToken}
            placeholder="AGENTHUB_MOBILE_DEV_TOKEN"
            autoCapitalize="none"
            autoComplete="off"
            onChange={(e) => onChange({ ...connection, deviceToken: e.target.value })}
          />
        </label>

        {error && <div className="error-banner">{error}</div>}

        <button type="button" className="primary-action full" onClick={onTest}>
          {connectionOk ? (
            <CheckCircle2 className="button-icon" aria-hidden="true" />
          ) : (
            <Wifi className="button-icon" aria-hidden="true" />
          )}
          {loading ? '测试中' : '测试连接'}
        </button>
      </section>
    </div>
  )
}

function isLoopbackUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return url.hostname === 'localhost' || url.hostname === '127.0.0.1' || url.hostname === '::1'
  } catch {
    return false
  }
}
