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
  const latestRunId = runs.length > 0 ? runs[runs.length - 1].id : null

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
          <span style={{ fontSize: '13px', color: 'var(--text-muted)', marginRight: '8px' }}>Tag:</span>
          <button className={`btn btn-sm ${!tagFilter ? 'active' : ''}`}
                  onClick={() => setTagFilter('')}>All</button>
          {['strong', 'weak', 'informational'].map(t => {
            const count = bugs.filter(b => b.bug_data?.tag === t).length
            if (!count) return null
            return (
              <button key={t} className={`btn btn-sm tag-filter-btn tag-${t} ${tagFilter === t ? 'active' : ''}`}
                      onClick={() => setTagFilter(tagFilter === t ? '' : t)}>
                {t} ({count})
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
            const isNew = latestRunId && bug.run_id === latestRunId && runs.length > 1
            return (
              <div key={bug.id} className={`bug-card ${d.severity || bug.status}`}>
                <div className="bug-header" onClick={() => setExpanded(isExpanded ? null : bug.id)}>
                  <span className={`severity-badge ${d.severity || 'unknown'}`}>
                    {d.severity || bug.status}
                  </span>
                  {d.tag && <span className={`tag-badge tag-${d.tag}`}>{d.tag}</span>}
                  {isNew && <span className="new-badge">NEW</span>}
                  <span className="bug-id">{d.id}</span>
                  <span className="bug-type">{d.vuln_type}</span>
                  <span className="bug-location">
                    {d.source_file ? `${d.source_file}:${d.line_range}` : d.url || ''}
                  </span>
                  <span className="bug-run">R#{runMap[bug.run_id] || '?'}</span>
                  <span className="expand-indicator">{isExpanded ? '-' : '+'}</span>
                </div>
                {isExpanded && (
                  <div className="bug-details">
                    <p><strong>Description:</strong> {d.description}</p>
                    <p><strong>Reasoning:</strong> {d.reasoning}</p>
                    <p><strong>Confidence:</strong> {d.confidence}</p>
                    <p><strong>Found by:</strong> {(d.found_by || []).join(', ')}</p>
                    <p><strong>Run:</strong> #{runMap[bug.run_id] || '?'}</p>
                    {d.poc && (
                      <div className="poc-section">
                        <strong>PoC ({d.poc.language}):</strong>
                        <pre>{d.poc.code}</pre>
                        <p>Result: {d.poc.execution_result}</p>
                        {d.poc.output && <pre className="poc-output">{d.poc.output}</pre>}
                      </div>
                    )}
                    {d.expanded_primitives && (
                      <div className="expansion-section">
                        <strong>Expanded Primitives:</strong>
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
                    )}
                    {d.triager_notes && (
                      <p><strong>Triager Notes:</strong> {d.triager_notes}</p>
                    )}
                    <pre className="raw-json">{JSON.stringify(d, null, 2)}</pre>
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
