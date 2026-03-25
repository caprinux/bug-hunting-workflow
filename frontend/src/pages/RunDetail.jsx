import React, { useState, useEffect, useRef } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { useAutoRefresh } from '../hooks/useAutoRefresh'
import PipelineVisualization from '../components/PipelineVisualization'
import StageOutputBrowser from '../components/StageOutputBrowser'
import ElapsedTimer from '../components/ElapsedTimer'

export default function RunDetail() {
  const { id: engagementId, runId } = useParams()
  const navigate = useNavigate()
  const [run, setRun] = useState(null)
  const [loading, setLoading] = useState(true)
  const [selectedStage, setSelectedStage] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  const [pausing, setPausing] = useState(false)
  const [resuming, setResuming] = useState(false)
  const [historicalEvents, setHistoricalEvents] = useState([])
  const [showAgentStream, setShowAgentStream] = useState(false)
  const streamRef = useRef(null)
  const { events, connected } = useWebSocket(engagementId)

  async function loadRun() {
    try {
      const data = await api.getRun(engagementId, runId)
      setRun(data)
    } catch (e) {
      console.error(e)
    }
    setLoading(false)
  }

  useAutoRefresh(loadRun, [engagementId, runId])

  useEffect(() => {
    api.getRunEvents(engagementId, runId)
      .then(data => setHistoricalEvents(data.events || []))
      .catch(() => {})
  }, [engagementId, runId])

  useEffect(() => {
    const stageUpdates = events.filter(e => e.run_id === runId && e.type === 'stage_update')
    if (stageUpdates.length > 0) loadRun()
  }, [events, runId])

  // Auto-scroll agent stream
  useEffect(() => {
    if (streamRef.current && showAgentStream) {
      streamRef.current.scrollTop = streamRef.current.scrollHeight
    }
  }, [events, showAgentStream])

  async function handleCancel() {
    if (!confirm('Cancel this run? In-flight subagents will be stopped and the run cannot be resumed.')) return
    setCancelling(true)
    try {
      await api.cancelRun(engagementId, runId)
      await loadRun()
    } catch (e) {
      console.error(e)
    }
    setCancelling(false)
  }

  async function handlePause() {
    if (!confirm('Pause this run? The current stage will be replayed from the start when you resume.')) return
    setPausing(true)
    try {
      await api.pauseRun(engagementId, runId)
      await loadRun()
    } catch (e) {
      console.error(e)
    }
    setPausing(false)
  }

  async function handleResume() {
    setResuming(true)
    try {
      await api.resumeRun(engagementId, runId)
      await loadRun()
    } catch (e) {
      console.error(e)
    }
    setResuming(false)
  }

  if (loading) return <div className="loading">Loading...</div>
  if (!run) return <div className="error-msg">Run not found</div>

  const stages = run.stages || []
  const liveEvents = events.filter(e => e.run_id === runId)
  // Merge historical + live, dedup
  const seenKeys = new Set()
  const allEvents = []
  for (const evt of [...historicalEvents, ...liveEvents]) {
    const key = `${evt.timestamp}-${evt.type}-${evt.stage}-${evt.data?.agent || ''}`
    if (!seenKeys.has(key)) {
      seenKeys.add(key)
      allEvents.push(evt)
    }
  }

  // Agent stream events (live only, not persisted)
  const agentStreamEvents = liveEvents.filter(e => e.type === 'agent_stream')

  // Per-agent stats from events
  const agentStats = {}
  for (const evt of allEvents) {
    if (evt.type === 'agent_progress' && evt.data?.agent) {
      agentStats[evt.data.agent] = evt.data
    }
  }

  // Run cost and token usage
  const totalCost = stages.reduce((sum, s) => sum + (s.cost_usd || 0), 0)
  const totalUsage = stages.reduce((acc, s) => {
    const meta = s.metadata ? (typeof s.metadata === 'string' ? JSON.parse(s.metadata) : s.metadata) : {}
    const u = meta.usage || {}
    return {
      input: acc.input + (u.input_tokens || 0),
      output: acc.output + (u.output_tokens || 0),
      cache_read: acc.cache_read + (u.cache_read_input_tokens || 0),
      cache_create: acc.cache_create + (u.cache_creation_input_tokens || 0),
    }
  }, { input: 0, output: 0, cache_read: 0, cache_create: 0 })
  const totalTokens = totalUsage.input + totalUsage.output + totalUsage.cache_read + totalUsage.cache_create

  return (
    <div className="page run-detail">
      <div className="page-header">
        <div>
          <Link to={`/engagements/${engagementId}`} className="back-link">Back to Engagement</Link>
          <h1>Run #{run.run_number}</h1>
          <div className="meta-row">
            <span className={`badge ${run.status}`}>{run.status}</span>
            <span className="run-type">{run.run_type}</span>
            {run.status === 'running' && (
              <ElapsedTimer startTime={run.created_at} active={true} />
            )}
            {run.current_stage && (run.status === 'running' || run.status === 'paused') && (
              <span className="current-stage">Current: {run.current_stage}</span>
            )}
            {totalCost > 0 && (
              <span className="cost">${totalCost.toFixed(3)}</span>
            )}
            <span className={`ws-status ${connected ? 'connected' : 'disconnected'}`}>
              {connected ? 'Live' : 'Disconnected'}
            </span>
          </div>
        </div>
        <div className="header-actions">
          {run.status === 'running' && (
            <>
              <button
                className={`btn btn-sm ${showAgentStream ? 'active' : ''}`}
                onClick={() => setShowAgentStream(!showAgentStream)}
              >
                {showAgentStream ? 'Hide' : 'Show'} Agent Stream
              </button>
              <button className="btn btn-secondary" onClick={handlePause} disabled={pausing || cancelling}>
                {pausing ? 'Pausing...' : 'Pause Run'}
              </button>
              <button className="btn btn-danger" onClick={handleCancel} disabled={cancelling}>
                {cancelling ? 'Cancelling...' : 'Stop Run'}
              </button>
            </>
          )}
          {(run.status === 'paused' || run.status === 'failed' || run.status === 'cancelled') && (
            <>
              <button className="btn btn-primary" onClick={handleResume} disabled={resuming}>
                {resuming ? 'Resuming...' : 'Resume Run'}
              </button>
              <button className="btn btn-danger" onClick={async () => {
                if (!confirm(`Delete Run #${run.run_number}? This removes all stage outputs, bugs, and events for this run.`)) return
                try {
                  await api.deleteRun(engagementId, runId)
                  navigate(`/engagements/${engagementId}`)
                } catch (e) { console.error(e) }
              }}>Delete Run</button>
            </>
          )}
          {run.status === 'completed' && (
            <button className="btn btn-danger" onClick={async () => {
              if (!confirm(`Delete Run #${run.run_number}? This removes all stage outputs, bugs, and events for this run.`)) return
              try {
                await api.deleteRun(engagementId, runId)
                navigate(`/engagements/${engagementId}`)
              } catch (e) { console.error(e) }
            }}>Delete Run</button>
          )}
        </div>
      </div>

      {run.rehunt_target && (
        <div className="rehunt-info">
          <strong>Re-hunt target:</strong> {run.rehunt_target}
        </div>
      )}

      {Object.keys(agentStats).length > 0 && (
        <div className="agent-status-bar">
          {Object.entries(agentStats).map(([agent, data]) => (
            <div key={agent} className={`agent-status-card ${data.status || 'idle'}`}>
              <span className={`status-dot ${data.running > 0 ? 'running' : 'completed'}`} />
              <span className="agent-name">{agent}</span>
              <span className="agent-detail">
                {data.running > 0 && `${data.running} active`}
                {data.succeeded > 0 && ` ${data.succeeded} done`}
                {data.failed > 0 && ` ${data.failed} failed`}
                {` / ${data.total_chunks} total`}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Run stats */}
      {(totalCost > 0 || totalTokens > 0) && (
        <div className="run-stats-bar">
          {totalCost > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">${totalCost.toFixed(2)}</span>
              <span className="run-stat-label">Cost</span>
            </div>
          )}
          {totalTokens > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">{formatTokens(totalTokens)}</span>
              <span className="run-stat-label">Total Tokens</span>
            </div>
          )}
          {totalUsage.input > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">{formatTokens(totalUsage.input)}</span>
              <span className="run-stat-label">Input</span>
            </div>
          )}
          {totalUsage.output > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">{formatTokens(totalUsage.output)}</span>
              <span className="run-stat-label">Output</span>
            </div>
          )}
          {totalUsage.cache_read > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">{formatTokens(totalUsage.cache_read)}</span>
              <span className="run-stat-label">Cache Read</span>
            </div>
          )}
          {totalUsage.cache_create > 0 && (
            <div className="run-stat">
              <span className="run-stat-value">{formatTokens(totalUsage.cache_create)}</span>
              <span className="run-stat-label">Cache Create</span>
            </div>
          )}
        </div>
      )}

      <h2>Pipeline</h2>
      <PipelineVisualization
        stages={stages}
        events={allEvents}
        onStageClick={setSelectedStage}
        runStatus={run.status}
      />

      {selectedStage && (
        <StageOutputBrowser
          engagementId={engagementId}
          runId={runId}
          stageName={selectedStage}
          onClose={() => setSelectedStage(null)}
        />
      )}

      {/* Live agent stream panel */}
      {showAgentStream && (
        <>
          <h2>Agent Stream</h2>
          <div className="agent-stream" ref={streamRef}>
            {agentStreamEvents.length === 0 ? (
              <div className="empty-state"><p>Waiting for agent output...</p></div>
            ) : (
              agentStreamEvents.slice(-200).map((evt, i) => (
                <div key={i} className="stream-entry">
                  <span className={`stream-agent ${evt.data?.agent_id || ''}`}>
                    {evt.data?.agent_id || '?'}
                  </span>
                  <span className="stream-text">{evt.data?.text || ''}</span>
                </div>
              ))
            )}
          </div>
        </>
      )}

      <h2>Event Log</h2>
      <div className="event-log">
        {allEvents.filter(e => e.type !== 'agent_stream').slice(-100).reverse().map((evt, i) => (
          <div key={i} className={`event-entry ${evt.type}`}>
            <span className="event-time">{new Date(evt.timestamp).toLocaleTimeString()}</span>
            <span className={`event-agent ${evt.data?.agent || ''}`}>
              {evt.data?.agent || ''}
            </span>
            <span className="event-stage">{evt.stage}</span>
            <span className="event-type">{evt.type}</span>
            <span className="event-data">
              {evt.data?.message || evt.data?.error || evt.data?.status || ''}
            </span>
          </div>
        ))}
        {allEvents.length === 0 && <div className="empty-state"><p>No events yet</p></div>}
      </div>
    </div>
  )
}

function formatTokens(n) {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`
  return String(n)
}
