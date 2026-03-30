import React, { useState, useEffect, useCallback } from 'react'
import { api } from '../utils/api'
import useTitle from '../hooks/useTitle'

function UsageBar({ percent, color }) {
  return (
    <div className="usage-bar-track">
      <div
        className="usage-bar-fill"
        style={{
          width: `${Math.min(100, percent || 0)}%`,
          background: percent > 80 ? 'var(--color-error)' : percent > 50 ? 'var(--color-warning)' : color || 'var(--accent)',
        }}
      />
    </div>
  )
}

function formatReset(value) {
  if (!value) return '—'
  // ISO string (Claude) or unix timestamp (Codex)
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  const now = new Date()
  const diffMs = date - now
  if (diffMs <= 0) return 'now'
  const mins = Math.floor(diffMs / 60000)
  if (mins < 60) return `${mins}m`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ${mins % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export default function Usage() {
  useTitle('Usage')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = useCallback(async () => {
    try {
      const result = await api.getUsage()
      setData(result)
      setError(null)
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  // Auto-refresh every 30s
  useEffect(() => {
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [load])

  if (loading) return <div className="empty-state"><p>Loading usage data...</p></div>

  const claude = data?.claude
  const codex = data?.codex

  return (
    <div className="page usage-page">
      <div className="page-header">
        <h1>Usage</h1>
        <button className="btn btn-secondary btn-sm" onClick={() => { setLoading(true); load() }}>Refresh</button>
      </div>

      <div className="usage-grid">
        {/* Claude Code */}
        <div className="usage-card">
          <div className="usage-card-header">
            <h2>Claude Code</h2>
            {claude?.error && <span className="badge warning">{claude.error}</span>}
          </div>
          {claude && !claude.error ? (
            <div className="usage-card-body">
              <div className="usage-meter">
                <div className="usage-meter-header">
                  <span>5-Hour Window</span>
                  <span className="usage-percent">{claude.five_hour?.utilization?.toFixed(1) ?? '?'}%</span>
                </div>
                <UsageBar percent={claude.five_hour?.utilization} />
                <span className="usage-reset">Resets in {formatReset(claude.five_hour?.resets_at)}</span>
              </div>
              <div className="usage-meter">
                <div className="usage-meter-header">
                  <span>7-Day Window</span>
                  <span className="usage-percent">{claude.seven_day?.utilization?.toFixed(1) ?? '?'}%</span>
                </div>
                <UsageBar percent={claude.seven_day?.utilization} />
                <span className="usage-reset">Resets in {formatReset(claude.seven_day?.resets_at)}</span>
              </div>
              {claude.seven_day_sonnet?.utilization > 0 && (
                <div className="usage-meter">
                  <div className="usage-meter-header">
                    <span>7-Day Sonnet</span>
                    <span className="usage-percent">{claude.seven_day_sonnet.utilization.toFixed(1)}%</span>
                  </div>
                  <UsageBar percent={claude.seven_day_sonnet.utilization} color="var(--color-info)" />
                </div>
              )}
            </div>
          ) : !claude?.error ? (
            <div className="usage-card-body"><p className="muted">No data</p></div>
          ) : null}
        </div>

        {/* Codex CLI */}
        <div className="usage-card">
          <div className="usage-card-header">
            <h2>Codex CLI</h2>
            {codex?.error && <span className="badge warning">{codex.error}</span>}
            {codex?.plan_type && <span className="badge">{codex.plan_type}</span>}
          </div>
          {codex && !codex.error ? (
            <div className="usage-card-body">
              <div className="usage-meter">
                <div className="usage-meter-header">
                  <span>5-Hour Window</span>
                  <span className="usage-percent">{codex.rate_limit?.primary_window?.used_percent ?? '?'}%</span>
                </div>
                <UsageBar percent={codex.rate_limit?.primary_window?.used_percent} />
                <span className="usage-reset">Resets in {formatReset(codex.rate_limit?.primary_window?.reset_at)}</span>
              </div>
              <div className="usage-meter">
                <div className="usage-meter-header">
                  <span>Weekly Window</span>
                  <span className="usage-percent">{codex.rate_limit?.secondary_window?.used_percent ?? '?'}%</span>
                </div>
                <UsageBar percent={codex.rate_limit?.secondary_window?.used_percent} />
                <span className="usage-reset">Resets in {formatReset(codex.rate_limit?.secondary_window?.reset_at)}</span>
              </div>
              {codex.rate_limit?.limit_reached && (
                <div style={{ padding: '8px 12px', background: 'var(--color-error)', color: '#fff', borderRadius: 6, marginTop: 8, fontSize: 13 }}>
                  Rate limit reached
                </div>
              )}
            </div>
          ) : !codex?.error ? (
            <div className="usage-card-body"><p className="muted">No data</p></div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
