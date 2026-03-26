import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useAutoRefresh } from '../hooks/useAutoRefresh'

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, informational: 4 }

export default function BugBrowser() {
  const { id } = useParams()
  const [bugs, setBugs] = useState([])
  const [runs, setRuns] = useState([])
  const [filter, setFilter] = useState('')
  const [runFilter, setRunFilter] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [expanded, setExpanded] = useState(null)
  const [loading, setLoading] = useState(true)

  async function loadData() {
    try {
      const [bugData, runData] = await Promise.all([
        api.listBugs(id, filter || undefined),
        api.listRuns(id),
      ])
      setBugs(bugData)
      setRuns(runData)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  useAutoRefresh(loadData, [id, filter])

  // Find the latest run to mark "new" bugs

  let filteredBugs = bugs
  if (runFilter) {
    filteredBugs = filteredBugs.filter(b => b.run_id === runFilter)
  }
  if (tagFilter) {
    filteredBugs = filteredBugs.filter(b => (b.bug_data?.tag || '') === tagFilter)
  }

  const sortedBugs = [...filteredBugs].sort((a, b) => {
    const sa = SEVERITY_ORDER[a.bug_data?.severity] ?? 5
    const sb = SEVERITY_ORDER[b.bug_data?.severity] ?? 5
    return sa - sb
  })

  const severityCounts = sortedBugs.reduce((acc, bug) => {
    const sev = bug.bug_data?.severity || 'unknown'
    acc[sev] = (acc[sev] || 0) + 1
    return acc
  }, {})

  // Map run_id to run_number for display
  const runMap = {}
  for (const r of runs) runMap[r.id] = r.run_number

  return (
    <div className="page bug-browser">
      <div className="page-header">
        <div>
          <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
          <h1>Bugs ({sortedBugs.length})</h1>
        </div>
        <div className="filters">
          {['', 'confirmed', 'cannot_validate', 'out_of_scope', 'informational', 'triage_failed', 'discarded'].map(s => (
            <button key={s} className={`btn btn-sm ${filter === s ? 'active' : ''}`}
                    onClick={() => { setFilter(s); setRunFilter('') }}>
              {s || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Run filter */}
      {runs.length > 1 && (
        <div className="filters" style={{ marginBottom: '12px' }}>
          <span style={{ fontSize: '13px', color: 'var(--text-muted)', marginRight: '8px' }}>Run:</span>
          <button className={`btn btn-sm ${!runFilter ? 'active' : ''}`}
                  onClick={() => setRunFilter('')}>All</button>
          {runs.map(r => (
            <button key={r.id} className={`btn btn-sm ${runFilter === r.id ? 'active' : ''}`}
                    onClick={() => setRunFilter(r.id)}>
              #{r.run_number}
            </button>
          ))}
        </div>
      )}

      {!loading && sortedBugs.length > 0 && (
        <div className="severity-summary">
          {['critical', 'high', 'medium', 'low', 'informational'].map(sev => {
            const count = severityCounts[sev]
            if (!count) return null
            return (
              <span key={sev} className={`severity-chip ${sev}`}>
                {count} {sev}
              </span>
            )
          })}
        </div>
      )}

      {/* Tag filter */}
      {!loading && bugs.some(b => b.bug_data?.tag) && (
        <div className="filters" style={{ marginBottom: '12px' }}>
          <span style={{ fontSize: '13px', color: 'var(--text-muted)', marginRight: '8px' }}>Confidence:</span>
          <button className={`btn btn-sm ${!tagFilter ? 'active' : ''}`}
                  onClick={() => setTagFilter('')}>All</button>
          {[['strong', 'Strong'], ['weak', 'Weak'], ['informational', 'Info']].map(([t, label]) => {
            const count = bugs.filter(b => b.bug_data?.tag === t).length
            if (!count) return null
            return (
              <button key={t} className={`btn btn-sm tag-filter-btn tag-${t} ${tagFilter === t ? 'active' : ''}`}
                      onClick={() => setTagFilter(tagFilter === t ? '' : t)}>
                {label} ({count})
              </button>
            )
          })}
        </div>
      )}

      {loading ? <div className="loading">Loading...</div> : (
        <div className="bug-list">
          {sortedBugs.map(bug => {
            const d = bug.bug_data || {}
            const isExpanded = expanded === bug.id
            return (
              <div key={bug.id} className={`bug-card ${d.severity || bug.status}`}>
                <div className="bug-header" onClick={() => setExpanded(isExpanded ? null : bug.id)}>
                  <span className={`severity-badge ${d.severity || 'unknown'}`}>
                    {d.severity || bug.status}
                  </span>
                  {d.tag && d.tag !== 'untagged' && (
                    <span className={`tag-badge tag-${d.tag}`} title={d.tag === 'strong' ? 'Strong confidence' : d.tag === 'weak' ? 'Weak confidence' : 'Informational'}>
                      %
                    </span>
                  )}
                  <span className="bug-type">{d.vuln_type}</span>
                  <span className="bug-location">
                    {d.source_file ? `${d.source_file}:${d.line_range}` : d.url || ''}
                  </span>
                  <span className="bug-run">R#{runMap[bug.run_id] || '?'}</span>
                  <span className="expand-indicator">{isExpanded ? '-' : '+'}</span>
                </div>
                {/* Inline reason preview for discarded/cannot-validate/out-of-scope */}
                {!isExpanded && (bug.status === 'discarded' || bug.status === 'cannot_validate' || bug.status === 'out_of_scope') && (
                  <div className="bug-reason-preview">
                    {d.triager_notes || d.cannot_validate_reason || d.scope_reasoning || d.reasoning || ''}
                  </div>
                )}
                {isExpanded && (
                  <div className="bug-details">
                    {d.vuln_class && (
                      <div className="bug-field">
                        <div className="bug-field-label">Vuln Class</div>
                        <div className="bug-field-value">{d.vuln_class}</div>
                      </div>
                    )}
                    <div className="bug-field">
                      <div className="bug-field-label">Description</div>
                      <div className="bug-field-value">{d.description}</div>
                    </div>
                    {d.security_impact && (
                      <div className="bug-field">
                        <div className="bug-field-label">Security Impact</div>
                        <div className="bug-field-value">{d.security_impact}</div>
                      </div>
                    )}
                    <div className="bug-field">
                      <div className="bug-field-label">Reasoning</div>
                      <div className="bug-field-value">{d.reasoning}</div>
                    </div>
                    {d.root_cause && (
                      <div className="bug-field">
                        <div className="bug-field-label">Root Cause</div>
                        <div className="bug-field-value">{d.root_cause}</div>
                      </div>
                    )}
                    <div className="bug-field">
                      <div className="bug-field-label">Found by</div>
                      <div className="bug-field-value">{(d.found_by || []).join(', ')} &middot; Run #{runMap[bug.run_id] || '?'}</div>
                    </div>
                    {d.poc && (
                      <div className="bug-field">
                        <div className="bug-field-label">PoC ({d.poc.language})</div>
                        <div className="bug-field-value">
                          <pre>{d.poc.code}</pre>
                          <p style={{ margin: '8px 0 0' }}>Result: {d.poc.execution_result}</p>
                          {d.poc.output && <pre className="poc-output">{d.poc.output}</pre>}
                        </div>
                      </div>
                    )}
                    {d.expanded_primitives && (
                      <div className="bug-field">
                        <div className="bug-field-label">Expanded Primitives</div>
                        <div className="bug-field-value">
                          {d.expanded_primitives.demonstrated?.map((exp, i) => (
                            <div key={i} className="expansion demonstrated">
                              <span className="badge success">Demonstrated</span> {exp.primitive}
                            </div>
                          ))}
                          {d.expanded_primitives.theoretical?.map((exp, i) => (
                            <div key={i} className="expansion theoretical">
                              <span className="badge warning">Theoretical</span> {exp.primitive}
                              <span className="muted"> ({exp.reason_not_demonstrated})</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    {d.triager_notes && (
                      <div className="bug-field">
                        <div className="bug-field-label">Triager Notes</div>
                        <div className="bug-field-value">{d.triager_notes}</div>
                      </div>
                    )}
                    {d.cannot_validate_reason && (
                      <div className="bug-field">
                        <div className="bug-field-label">Cannot Validate Reason</div>
                        <div className="bug-field-value">{d.cannot_validate_reason}</div>
                      </div>
                    )}
                    {d.scope_reasoning && (
                      <div className="bug-field">
                        <div className="bug-field-label">Scope Reasoning</div>
                        <div className="bug-field-value">{d.scope_reasoning}</div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
          {sortedBugs.length === 0 && <div className="empty-state"><p>No bugs found</p></div>}
        </div>
      )}
    </div>
  )
}
