import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'

export default function IntelBrowser() {
  const { id } = useParams()
  const [intel, setIntel] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listBugs(id, 'informational')
      .then(setIntel)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [id])

  return (
    <div className="page intel-browser">
      <div className="page-header">
        <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
        <h1>Intelligence ({intel.length})</h1>
      </div>

      {loading ? <div className="loading">Loading...</div> : (
        <div className="intel-list">
          {intel.map(item => {
            const d = item.bug_data || {}
            return (
              <div key={item.id} className="intel-card">
                <div className="intel-header">
                  <span className="badge informational">Info</span>
                  <span className="intel-type">{d.vuln_type || 'Information Disclosure'}</span>
                  <span className="intel-location">
                    {d.source_file || d.url || ''}
                  </span>
                </div>
                <p>{d.description}</p>
                {d.triager_notes && <p className="muted">{d.triager_notes}</p>}
              </div>
            )
          })}
          {intel.length === 0 && <div className="empty-state">No intelligence findings</div>}
        </div>
      )}
    </div>
  )
}
