import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'

const SEVERITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3, informational: 4 }

export default function BugBrowser() {
  const { id } = useParams()
  const [bugs, setBugs] = useState([])
  const [filter, setFilter] = useState('')
  const [expanded, setExpanded] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { loadBugs() }, [id, filter])

  async function loadBugs() {
    try {
      const data = await api.listBugs(id, filter || undefined)
      setBugs(data)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  const sortedBugs = [...bugs].sort((a, b) => {
    const sa = SEVERITY_ORDER[a.bug_data?.severity] ?? 5
    const sb = SEVERITY_ORDER[b.bug_data?.severity] ?? 5
    return sa - sb
  })

  return (
    <div className="page bug-browser">
      <div className="page-header">
        <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
        <h1>Bugs ({bugs.length})</h1>
        <div className="filters">
          {['', 'confirmed', 'validated', 'cannot_validate', 'informational', 'discarded'].map(s => (
            <button key={s} className={`btn btn-sm ${filter === s ? 'active' : ''}`}
                    onClick={() => setFilter(s)}>
              {s || 'All'}
            </button>
          ))}
        </div>
      </div>

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
                  <span className="bug-id">{d.id}</span>
                  <span className="bug-type">{d.vuln_type}</span>
                  <span className="bug-location">
                    {d.source_file ? `${d.source_file}:${d.line_range}` : d.url || ''}
                  </span>
                  <span className="expand-indicator">{isExpanded ? '-' : '+'}</span>
                </div>
                {isExpanded && (
                  <div className="bug-details">
                    <p><strong>Description:</strong> {d.description}</p>
                    <p><strong>Reasoning:</strong> {d.reasoning}</p>
                    <p><strong>Confidence:</strong> {d.confidence}</p>
                    <p><strong>Found by:</strong> {(d.found_by || []).join(', ')}</p>
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
          {sortedBugs.length === 0 && <div className="empty-state">No bugs found</div>}
        </div>
      )}
    </div>
  )
}
