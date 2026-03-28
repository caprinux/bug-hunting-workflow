import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useAutoRefresh } from '../hooks/useAutoRefresh'

export default function Dashboard() {
  const [engagements, setEngagements] = useState([])
  const [loading, setLoading] = useState(true)
  const { events, connected } = useWebSocket()

  async function loadEngagements() {
    try {
      const data = await api.listEngagements()
      setEngagements(data)
    } catch (e) {
      console.error('Failed to load engagements:', e)
    }
    setLoading(false)
  }

  useAutoRefresh(loadEngagements, [])

  useEffect(() => {
    const completionEvents = events.filter(e => e.type === 'completion')
    if (completionEvents.length > 0) loadEngagements()
  }, [events])

  const stats = {
    total: engagements.length,
    active: engagements.filter(e => e.status === 'active' || e.status === 'running').length,
    runs: engagements.reduce((sum, e) => sum + (e.runs?.length || 0), 0),
    cost: engagements.reduce((sum, e) => sum + (e.cost_total_usd || 0), 0),
  }

  return (
    <div className="page dashboard">
      <div className="page-header">
        <h1>Engagements</h1>
        <div className="header-actions">
          <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
            {connected ? 'Live' : 'Disconnected'}
          </span>
          <Link to="/engagements/new" className="btn btn-primary">New Engagement</Link>
        </div>
      </div>

      {!loading && engagements.length > 0 && (
        <div className="stats-bar">
          <div className="stat-card">
            <span className="stat-value">{stats.total}</span>
            <span className="stat-label">Engagements</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{stats.active}</span>
            <span className="stat-label">Active</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">{stats.runs}</span>
            <span className="stat-label">Total Runs</span>
          </div>
          <div className="stat-card">
            <span className="stat-value">${stats.cost.toFixed(2)}</span>
            <span className="stat-label">Total Cost</span>
          </div>
        </div>
      )}

      {loading ? (
        <div className="loading">Loading engagements...</div>
      ) : engagements.length === 0 ? (
        <div className="empty-state">
          <svg className="empty-icon" width="48" height="48" viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="24" cy="24" r="18" opacity="0.2" />
            <circle cx="24" cy="24" r="8" opacity="0.4" />
            <path d="M24 4v8M24 36v8M4 24h8M36 24h8" opacity="0.3" />
          </svg>
          <p>No engagements yet</p>
          <Link to="/engagements/new" className="btn btn-primary" style={{ marginTop: '12px' }}>
            Create your first engagement
          </Link>
        </div>
      ) : (
        <div className="engagement-grid">
          {engagements.map((eng, i) => (
            <Link key={eng.id} to={`/engagements/${eng.id}`} className="engagement-card" style={{ '--i': i }}>
              <div className="card-header">
                <h3>{eng.name}</h3>
                {(() => {
                  const hasRunning = eng.runs?.some(r => r.status === 'running')
                  const hasRuns = eng.runs?.length > 0
                  const label = hasRunning ? 'running' : hasRuns ? 'idle' : 'new'
                  const cls = hasRunning ? 'running' : hasRuns ? 'completed' : 'pending'
                  return <span className={`badge ${cls}`}>{label}</span>
                })()}
              </div>
              <div className="card-meta">
                <span className={`type-badge ${eng.type}`}>
                  {eng.type === 'source_code' ? 'Source Code' : 'Black Box'}
                </span>
                <span className="run-count">{eng.runs?.length || 0} runs</span>
                {eng.cost_total_usd > 0 && (
                  <span className="cost">${eng.cost_total_usd.toFixed(2)}</span>
                )}
              </div>
              {eng.bug_counts?.active > 0 && (
                <div className="card-bugs">
                  {['critical', 'high', 'medium', 'low'].map(sev => {
                    const count = eng.bug_counts.by_severity?.[sev]
                    if (!count) return null
                    return <span key={sev} className={`severity-chip-sm ${sev}`}>{count}</span>
                  })}
                  <span className="bug-total">{eng.bug_counts.active} bugs</span>
                </div>
              )}
              <div className="card-date">
                {new Date(eng.created_at).toLocaleDateString(undefined, {
                  year: 'numeric', month: 'short', day: 'numeric'
                })}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
