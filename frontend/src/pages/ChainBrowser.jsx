import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'
import useTitle from '../hooks/useTitle'

export default function ChainBrowser() {
  useTitle('Chains')
  const { id } = useParams()
  const [chains, setChains] = useState([])
  const [expanded, setExpanded] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.listChains(id).then(setChains).catch(console.error).finally(() => setLoading(false))
  }, [id])

  return (
    <div className="page chain-browser">
      <div className="page-header">
        <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
        <h1>Chains ({chains.length})</h1>
      </div>

      {loading ? <div className="loading">Loading...</div> : (
        <div className="chain-list">
          {chains.map(chain => {
            const d = chain.chain_data || {}
            const isExpanded = expanded === chain.id
            return (
              <div key={chain.id} className={`chain-card ${d.status}`}>
                <div className="chain-header" onClick={() => setExpanded(isExpanded ? null : chain.id)}>
                  <span className={`badge ${d.status}`}>{d.status}</span>
                  <span className={`severity-badge ${d.severity}`}>{d.severity}</span>
                  <span className="chain-id">{d.id}</span>
                  <span className="chain-desc">{d.description}</span>
                  <span className="expand-indicator">{isExpanded ? '-' : '+'}</span>
                </div>
                {isExpanded && (
                  <div className="chain-details">
                    <p><strong>Bugs:</strong> {(d.bug_ids || []).join(' + ')}</p>
                    <p><strong>Combined Impact:</strong> {d.combined_impact}</p>
                    <p><strong>Execution Order:</strong></p>
                    <pre>{d.execution_order}</pre>
                    <pre className="raw-json">{JSON.stringify(d, null, 2)}</pre>
                  </div>
                )}
              </div>
            )
          })}
          {chains.length === 0 && <div className="empty-state">No chains found</div>}
        </div>
      )}
    </div>
  )
}
