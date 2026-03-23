import React, { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'

export default function Dashboard() {
  const [engagements, setEngagements] = useState([])
  const [loading, setLoading] = useState(true)
  const { events, connected } = useWebSocket()

  useEffect(() => { loadEngagements() }, [])

  useEffect(() => {
    const completionEvents = events.filter(e => e.type === 'completion')
    if (completionEvents.length > 0) loadEngagements()
  }, [events])

  async function loadEngagements() {
    try {
      const data = await api.listEngagements()
      setEngagements(data)
    } catch (e) {
      console.error('Failed to load engagements:', e)
    }
    setLoading(false)
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

      {loading ? (
        <div className="loading">Loading engagements...</div>
      ) : engagements.length === 0 ? (
        <div className="empty-state">
          <p>No engagements yet.</p>
          <Link to="/engagements/new" className="btn btn-primary">Create your first engagement</Link>
        </div>
      ) : (
        <div className="engagement-grid">
          {engagements.map(eng => (
            <Link key={eng.id} to={`/engagements/${eng.id}`} className="engagement-card">
              <div className="card-header">
                <h3>{eng.name}</h3>
                <span className={`badge ${eng.status}`}>{eng.status}</span>
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
              <div className="card-date">
                {new Date(eng.created_at).toLocaleDateString()}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
