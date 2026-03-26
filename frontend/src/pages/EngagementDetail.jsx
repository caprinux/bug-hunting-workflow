import React, { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useAutoRefresh } from '../hooks/useAutoRefresh'

export default function EngagementDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [engagement, setEngagement] = useState(null)
  const [bugs, setBugs] = useState([])
  const [chains, setChains] = useState([])
  const [loading, setLoading] = useState(true)
  const [startingRun, setStartingRun] = useState(false)
  const [rehuntTarget, setRehuntTarget] = useState('')
  const [showRehunt, setShowRehunt] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showDetails, setShowDetails] = useState(false)
  const [engConfig, setEngConfig] = useState({})
  const [savingConfig, setSavingConfig] = useState(false)
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
      // Extract editable config
      const cfg = eng.config || {}
      setEngConfig({
        agents: cfg.bug_hunter?.agents || cfg.broad_bug_hunter?.agents || ['claude', 'codex'],
        codex_model: cfg.bug_hunter?.codex_model || cfg.broad_bug_hunter?.codex_model || 'gpt-5.4',
        iterations: cfg.bug_hunter?.iterations || cfg.broad_bug_hunter?.iterations || 1,
        mode: cfg.bug_hunter?.mode || 'parallel',
        subagent_timeout: cfg.pipeline?.subagent_timeout || 3600,
        perfectionist_enabled: cfg.perfectionist?.enabled === true,
        bug_chainer_enabled: cfg.bug_chainer?.enabled === true,
      })
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
            <span className={`badge ${hasActiveRun ? 'running' : runs.length > 0 ? 'completed' : 'pending'}`}>
              {hasActiveRun ? 'running' : runs.length > 0 ? 'idle' : 'new'}
            </span>
            <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
              {connected ? 'Live' : 'Disconnected'}
            </span>
            {engagement.cost_total_usd > 0 && (
              <span className="cost">Total cost: ${engagement.cost_total_usd.toFixed(2)}</span>
            )}
          </div>
        </div>
        <div className="header-actions">
          <button className={`btn btn-secondary ${showDetails ? 'active' : ''}`} onClick={() => setShowDetails(!showDetails)}>Details</button>
          <Link to={`/engagements/${id}/report`} className="btn btn-secondary">Report</Link>
          <Link to={`/engagements/${id}/bugs`} className="btn btn-secondary">Bugs</Link>
          <Link to={`/engagements/${id}/chains`} className="btn btn-secondary">Chains</Link>
          <Link to={`/engagements/${id}/intel`} className="btn btn-secondary">Intel</Link>
          <Link to={`/engagements/${id}/chat`} className="btn btn-secondary">Chat</Link>
        </div>
      </div>

      {!hasActiveRun && (
        <div className="engagement-actions">
          <button className="btn btn-primary" onClick={() => startRun()} disabled={startingRun}>
            {runs.length === 0 ? 'Start Pipeline' : 'New Run'}
          </button>
          <button className="btn btn-secondary" onClick={() => setShowRehunt(!showRehunt)}>
            Re-hunt
          </button>
          <button className="btn btn-secondary" onClick={() => setShowSettings(!showSettings)}>
            Settings
          </button>
          <button className="btn btn-danger" onClick={async () => {
            if (!confirm(`Delete engagement "${engagement.name}"? This removes all runs, bugs, and output files permanently.`)) return
            try {
              await api.deleteEngagement(id)
              navigate('/')
            } catch (e) { console.error(e) }
          }}>Delete</button>
        </div>
      )}

      {/* Engagement details panel */}
      {showDetails && engagement.config && (() => {
        const cfg = engagement.config
        const eng = cfg.engagement || {}
        const scope = eng.scope_definition || ''
        const infra = eng.infra_config || ''
        const sections = scope.split('\n\n').filter(Boolean)

        return (
          <div className="engagement-details-panel">
            {eng.source_repo && (
              <DetailSection title="Source Repositories">
                {eng.source_repo.split(',').map((r, i) => (
                  <div key={i} className="detail-mono">{r.trim()}</div>
                ))}
              </DetailSection>
            )}
            {eng.source_path && (
              <DetailSection title="Source Path">
                <div className="detail-mono">{eng.source_path}</div>
              </DetailSection>
            )}
            {sections.map((section, i) => {
              const lines = section.split('\n')
              const title = lines[0].replace(/:/g, '').trim()
              const body = lines.slice(1).join('\n').trim()
              return (
                <DetailSection key={i} title={title}>
                  <pre className="detail-pre">{body}</pre>
                </DetailSection>
              )
            })}
            {infra && (
              <DetailSection title="Infrastructure">
                <pre className="detail-pre">{infra}</pre>
              </DetailSection>
            )}
            {cfg.bug_hunter && (
              <DetailSection title="Bug Hunter Config">
                <div className="detail-meta">
                  <span>Agents: {(cfg.bug_hunter.agents || []).join(', ')}</span>
                  <span>Iterations: {cfg.bug_hunter.iterations || 1}</span>
                  {cfg.bug_hunter.codex_model && <span>Codex model: {cfg.bug_hunter.codex_model}</span>}
                </div>
              </DetailSection>
            )}
          </div>
        )
      })()}

      {showRehunt && (
        <div className="rehunt-form">
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            The Bug Hunter will read its previous BUGS.json and attack_surfaces.json, then continue
            scanning surfaces it hasn't covered yet. Add specific instructions below, or leave as default.
          </p>
          <textarea value={rehuntTarget} onChange={e => setRehuntTarget(e.target.value)}
                    placeholder="Continue scanning unscanned attack surfaces and look for bugs not yet found. Focus on areas marked not_scanned."
                    rows={3} />
          <div style={{ display: 'flex', gap: '8px' }}>
            <button className="btn btn-primary" onClick={() => startRun('rehunt',
              rehuntTarget.trim() || 'Continue scanning unscanned attack surfaces and look for bugs not yet found. Focus on areas marked not_scanned.'
            )}>
              Start Re-hunt
            </button>
            <button className="btn btn-secondary" onClick={() => setShowRehunt(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Settings panel */}
      {showSettings && !hasActiveRun && (
        <div className="engagement-settings-panel">
          <h3>Run Settings</h3>
          {(() => {
            const hasRuns = runs.length > 0
            const modeLocked = hasRuns
            const agentsLocked = hasRuns && engConfig.mode === 'sequential'
            return (<>
          <div className="config-grid">
            <div className="form-group" style={agentsLocked ? { opacity: 0.6 } : {}}>
              <label>Bug Hunter Agents {agentsLocked && <span style={{ fontSize: '10px', color: 'var(--color-warning)' }}>(locked — sequential mode)</span>}</label>
              <div className="agent-checkboxes">
                <label className="toggle-label">
                  <input type="checkbox" disabled={agentsLocked} checked={engConfig.agents?.includes('claude')}
                    onChange={e => {
                      const next = e.target.checked
                        ? [...new Set([...(engConfig.agents || []), 'claude'])]
                        : (engConfig.agents || []).filter(a => a !== 'claude')
                      if (next.length > 0) setEngConfig(c => ({ ...c, agents: next }))
                    }} />
                  <span>Claude</span>
                </label>
                <label className="toggle-label">
                  <input type="checkbox" disabled={agentsLocked} checked={engConfig.agents?.includes('codex')}
                    onChange={e => {
                      const next = e.target.checked
                        ? [...new Set([...(engConfig.agents || []), 'codex'])]
                        : (engConfig.agents || []).filter(a => a !== 'codex')
                      if (next.length > 0) setEngConfig(c => ({ ...c, agents: next }))
                    }} />
                  <span>Codex</span>
                </label>
              </div>
            </div>
            <div className="form-group">
              <label>Hunt Iterations</label>
              <input type="number" min="1" value={engConfig.iterations || 1}
                onChange={e => setEngConfig(c => ({ ...c, iterations: Math.max(1, parseInt(e.target.value) || 1) }))} />
              <small className="muted" style={{ fontSize: '11px' }}>Bug Hunter runs N times before proceeding to validation</small>
            </div>
            <div className="form-group" style={modeLocked ? { opacity: 0.6 } : {}}>
              <label>Agent Mode {modeLocked && <span style={{ fontSize: '10px', color: 'var(--color-warning)' }}>(locked)</span>}</label>
              <select disabled={modeLocked} value={engConfig.mode} onChange={e => setEngConfig(c => ({ ...c, mode: e.target.value }))}>
                <option value="parallel">Parallel</option>
                <option value="sequential">Sequential</option>
              </select>
              <small className="muted" style={{ fontSize: '11px' }}>
                {engConfig.mode === 'sequential'
                  ? `Agents run one at a time (${(engConfig.agents || []).join(' → ')}), sharing notes`
                  : 'Agents run concurrently, each with their own notes'}
              </small>
            </div>
            <div className="form-group">
              <label>Subagent Timeout (s)</label>
              <input type="number" value={engConfig.subagent_timeout || 3600}
                onChange={e => setEngConfig(c => ({ ...c, subagent_timeout: parseInt(e.target.value) || 3600 }))} />
            </div>
            <div className="form-group">
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input type="checkbox" checked={engConfig.perfectionist_enabled}
                  onChange={e => setEngConfig(c => ({ ...c, perfectionist_enabled: e.target.checked }))} />
                Perfectionist
              </label>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Expand validated bugs to maximum impact with additional PoCs</span>
            </div>
            <div className="form-group">
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input type="checkbox" checked={engConfig.bug_chainer_enabled}
                  onChange={e => setEngConfig(c => ({ ...c, bug_chainer_enabled: e.target.checked }))} />
                Bug Chainer
              </label>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>Chain bugs together into attack paths</span>
            </div>
          </div>
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
            <button className="btn btn-primary" disabled={savingConfig} onClick={async () => {
                setSavingConfig(true)
                try {
                  await api.updateEngagementConfig(id, {
                    bug_hunter: { agents: engConfig.agents, codex_model: engConfig.codex_model, iterations: engConfig.iterations, mode: engConfig.mode },
                    pipeline: { subagent_timeout: engConfig.subagent_timeout },
                    perfectionist: { enabled: engConfig.perfectionist_enabled },
                    bug_chainer: { enabled: engConfig.bug_chainer_enabled },
                  })
                  setShowSettings(false)
                  await loadAll()
                } catch (e) { console.error(e) }
                setSavingConfig(false)
              }}>
                {savingConfig ? 'Saving...' : 'Save Settings'}
              </button>
            <button className="btn btn-secondary" onClick={() => setShowSettings(false)}>Close</button>
          </div>
          </>)})()}
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
            <div key={run.id} className="run-card-row">
              <Link to={`/engagements/${id}/runs/${run.id}`} className="run-card" style={{ flex: 1 }}>
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
              {run.status !== 'running' && (
                <button className="btn btn-sm btn-danger run-delete-btn" onClick={async (e) => {
                  e.preventDefault()
                  if (!confirm(`Delete Run #${run.run_number}?`)) return
                  try {
                    await api.deleteRun(id, run.id)
                    await loadAll()
                  } catch (err) { console.error(err) }
                }}>Delete</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function DetailSection({ title, children }) {
  return (
    <div className="detail-section">
      <h4 className="detail-section-title">{title}</h4>
      {children}
    </div>
  )
}
