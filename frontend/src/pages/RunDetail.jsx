import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import PipelineVisualization from '../components/PipelineVisualization'
import StageOutputBrowser from '../components/StageOutputBrowser'

export default function RunDetail() {
  const { id: engagementId, runId } = useParams()
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selectedStage, setSelectedStage] = useState(null)
  const { events, connected } = useWebSocket(engagementId)

  useEffect(() => { loadRun() }, [runId])
  useEffect(() => {
    const stageUpdates = events.filter(e => e.run_id === runId && e.type === 'stage_update')
    if (stageUpdates.length > 0) loadRun()
  }, [events, runId])

  async function loadRun() {
    try {
      const data = await api.getRun(engagementId, runId)
      setRun(data)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  if (loading) return <div className="loading">Loading...</div>
  if (!run) return <div className="error-msg">Run not found</div>

  const stages = run.stages || []
  const runEvents = events.filter(e => e.run_id === runId)

  return (
    <div className="page run-detail">
      <div className="page-header">
        <div>
          <Link to={`/engagements/${engagementId}`} className="back-link">Back to Engagement</Link>
          <h1>Run #{run.run_number}</h1>
          <div className="meta-row">
            <span className={`badge ${run.status}`}>{run.status}</span>
            <span className="run-type">{run.run_type}</span>
            {run.current_stage && run.status === 'running' && (
              <span className="current-stage">Current: {run.current_stage}</span>
            )}
            <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
              {connected ? 'Live' : 'Disconnected'}
            </span>
          </div>
        </div>
      </div>

      {run.rehunt_target && (
        <div className="rehunt-info">
          <strong>Re-hunt target:</strong> {run.rehunt_target}
        </div>
      )}

      <h2>Pipeline</h2>
      <PipelineVisualization
        stages={stages}
        events={runEvents}
        onStageClick={setSelectedStage}
      />

      {selectedStage && (
        <StageOutputBrowser
          engagementId={engagementId}
          runId={runId}
          stageName={selectedStage}
          onClose={() => setSelectedStage(null)}
        />
      )}

      <h2>Event Log</h2>
      <div className="event-log">
        {runEvents.slice(-50).reverse().map((evt, i) => (
          <div key={i} className={`event-entry ${evt.type}`}>
            <span className="event-time">{new Date(evt.timestamp).toLocaleTimeString()}</span>
            <span className="event-stage">{evt.stage}</span>
            <span className="event-type">{evt.type}</span>
            <span className="event-data">
              {evt.data?.message || evt.data?.error || evt.data?.status || ''}
            </span>
          </div>
        ))}
        {runEvents.length === 0 && <div className="empty-state">No events yet</div>}
      </div>
    </div>
  )
}
