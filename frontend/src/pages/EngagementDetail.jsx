import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useAutoRefresh } from '../hooks/useAutoRefresh'

export default function EngagementDetail() {
  const { id } = useParams()
  const [engagement, setEngagement] = useState(null)
  const [bugs, setBugs] = useState([])
  const [chains, setChains] = useState([])
  const [loading, setLoading] = useState(true)
  const [startingRun, setStartingRun] = useState(false)
  const [rehuntTarget, setRehuntTarget] = useState('')
  const [showRehunt, setShowRehunt] = useState(false)
  const { events, connected } = useWebSocket(id)

  async function loadAll() {
    try {
      const [eng, bugData, chainData] = await Promise.all([
        api.getEngagement(id),
        api.listBugs(id).catch(() => []),
        api.listChains(id).catch(() => []),
      ])
      setEngagement(eng)
      setBugs(bugData)
      setChains(chainData)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  useAutoRefresh(loadAll, [id])

  useEffect(() => {
    const updates = events.filter(e => e.type === 'completion' || e.type === 'stage_update')
    if (updates.length > 0) loadAll()
  }, [events])

  async function startRun(type = 'initial', target = '') {
    setStartingRun(true)
    try {
      await api.startRun(id, { run_type: type, rehunt_target: target })
      await loadAll()
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

  // Summary stats
  const confirmedBugs = bugs.filter(b => b.status === 'confirmed')
  const cannotValidate = bugs.filter(b => b.status === 'cannot_validate')
  const triageFailed = bugs.filter(b => b.status === 'triage_failed')
  const informational = bugs.filter(b => b.status === 'informational')

  const severityCounts = confirmedBugs.reduce((acc, b) => {
    const sev = b.bug_data?.severity || 'unknown'
    acc[sev] = (acc[sev] || 0) + 1
    return acc
  }, {})

  const demonstratedChains = chains.filter(c => c.chain_data?.status === 'demonstrated')
  const proposedChains = chains.filter(c => c.chain_data?.status === 'proposed')

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

      {/* Summary panel */}
      {(confirmedBugs.length > 0 || cannotValidate.length > 0 || chains.length > 0) && (
        <div className="engagement-summary">
          <div className="summary-section">
            <h3>Confirmed Bugs</h3>
            <div className="severity-summary">
              {['critical', 'high', 'medium', 'low'].map(sev => {
                const count = severityCounts[sev]
                if (!count) return null
                return (
                  <span key={sev} className={`severity-chip ${sev}`}>
                    {count} {sev}
                  </span>
                )
              })}
              {confirmedBugs.length === 0 && <span className="muted">None yet</span>}
            </div>
          </div>

          <div className="summary-section">
            <h3>Chains</h3>
            <div className="summary-counts">
              {demonstratedChains.length > 0 && (
                <span className="badge success">{demonstratedChains.length} demonstrated</span>
              )}
              {proposedChains.length > 0 && (
                <span className="badge warning">{proposedChains.length} proposed</span>
              )}
              {chains.length === 0 && <span className="muted">None yet</span>}
            </div>
          </div>

          <div className="summary-section">
            <h3>Review Queue</h3>
            <div className="summary-counts">
              {cannotValidate.length > 0 && (
                <span className="badge">{cannotValidate.length} cannot validate</span>
              )}
              {triageFailed.length > 0 && (
                <span className="badge warning">{triageFailed.length} triage failed</span>
              )}
              {informational.length > 0 && (
                <span className="badge informational">{informational.length} informational</span>
              )}
              {cannotValidate.length === 0 && triageFailed.length === 0 && informational.length === 0 && (
                <span className="muted">Empty</span>
              )}
            </div>
          </div>
        </div>
      )}

      <h2>Runs</h2>
      {runs.length === 0 ? (
        <div className="empty-state"><p>No runs yet. Click "Start Pipeline" to begin.</p></div>
      ) : (
        <div className="runs-list">
          {runs.map(run => (
            <Link key={run.id} to={`/engagements/${id}/runs/${run.id}`} className="run-card">
              <div className="run-header">
                <span className="run-number">Run #{run.run_number}</span>
                <span className={`badge ${run.status}`}>{run.status}</span>
                <span className="run-type">{run.run_type}</span>
                {run.cost_usd > 0 && <span className="cost">${run.cost_usd.toFixed(3)}</span>}
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
