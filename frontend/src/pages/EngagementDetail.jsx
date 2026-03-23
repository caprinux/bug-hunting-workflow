import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'

export default function EngagementDetail() {
  const { id } = useParams()
  const [engagement, setEngagement] = useState(null)
  const [loading, setLoading] = useState(true)
  const [startingRun, setStartingRun] = useState(false)
  const [rehuntTarget, setRehuntTarget] = useState('')
  const [showRehunt, setShowRehunt] = useState(false)
  const { events, connected } = useWebSocket(id)

  useEffect(() => { loadEngagement() }, [id])
  useEffect(() => {
    const updates = events.filter(e => e.type === 'completion' || e.type === 'stage_update')
    if (updates.length > 0) loadEngagement()
  }, [events])

  async function loadEngagement() {
    try {
      const data = await api.getEngagement(id)
      setEngagement(data)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  async function startRun(type = 'initial', target = '') {
    setStartingRun(true)
    try {
      await api.startRun(id, { run_type: type, rehunt_target: target })
      await loadEngagement()
    } catch (e) {
      console.error(e)
    }
    setStartingRun(false)
    setShowRehunt(false)
    setRehuntTarget('')
  }

  if (loading) return <div className="loading">Loading...</div>
  if (!engagement) return <div className="error-msg">Engagement not found</div>

  const runs = engagement.runs || []
  const hasActiveRun = runs.some(r => r.status === 'running')

  return (
    <div className="page engagement-detail">
      <div className="page-header">
        <div>
          <h1>{engagement.name}</h1>
          <div className="meta-row">
            <span className={`type-badge ${engagement.type}`}>
              {engagement.type === 'source_code' ? 'Source Code Audit' : 'Black Box Pentest'}
            </span>
            <span className={`badge ${engagement.status}`}>{engagement.status}</span>
            <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
              {connected ? 'Live' : 'Disconnected'}
            </span>
            {engagement.cost_total_usd > 0 && (
              <span className="cost">Total cost: ${engagement.cost_total_usd.toFixed(2)}</span>
            )}
          </div>
        </div>
        <div className="header-actions">
          <Link to={`/engagements/${id}/bugs`} className="btn btn-secondary">Bugs</Link>
          <Link to={`/engagements/${id}/chains`} className="btn btn-secondary">Chains</Link>
          <Link to={`/engagements/${id}/intel`} className="btn btn-secondary">Intel</Link>
          {!hasActiveRun && (
            <>
              <button className="btn btn-primary" onClick={() => startRun()} disabled={startingRun}>
                {runs.length === 0 ? 'Start Pipeline' : 'New Run'}
              </button>
              <button className="btn btn-secondary" onClick={() => setShowRehunt(!showRehunt)}>
                Re-hunt
              </button>
            </>
          )}
        </div>
      </div>

      {showRehunt && (
        <div className="rehunt-form">
          <textarea value={rehuntTarget} onChange={e => setRehuntTarget(e.target.value)}
                    placeholder="Describe what to hunt for (e.g., 'Find stored XSS in the admin panel to chain with confirmed CSRF bug-004')"
                    rows={3} />
          <button className="btn btn-primary" onClick={() => startRun('rehunt', rehuntTarget)}
                  disabled={!rehuntTarget.trim()}>
            Start Re-hunt
          </button>
        </div>
      )}

      <h2>Runs</h2>
      {runs.length === 0 ? (
        <div className="empty-state">No runs yet. Click "Start Pipeline" to begin.</div>
      ) : (
        <div className="runs-list">
          {runs.map(run => (
            <Link key={run.id} to={`/engagements/${id}/runs/${run.id}`} className="run-card">
              <div className="run-header">
                <span className="run-number">Run #{run.run_number}</span>
                <span className={`badge ${run.status}`}>{run.status}</span>
                <span className="run-type">{run.run_type}</span>
              </div>
              {run.rehunt_target && (
                <div className="run-rehunt">Target: {run.rehunt_target}</div>
              )}
              {run.current_stage && run.status === 'running' && (
                <div className="run-stage">Current: {run.current_stage}</div>
              )}
              <div className="run-date">{new Date(run.created_at).toLocaleString()}</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
