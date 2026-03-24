import React, { useState } from 'react'
import ElapsedTimer from './ElapsedTimer'

const STAGE_LABELS = {
  setup: 'Setup',
  scoper: 'Scoper',
  bug_hunter: 'Bug Hunter',
  deduplicator: 'De-duplicator',
  scope_validator: 'Scope Check',
  strict_validator: 'Validator',
  perfectionist: 'Perfectionist',
  strict_triager: 'Triager',
  bug_chainer: 'Bug Chainer',
}

const STATUS_COLORS = {
  pending: 'var(--color-muted)',
  running: 'var(--color-info)',
  completed: 'var(--color-success)',
  failed: 'var(--color-error)',
  skipped: 'var(--color-muted)',
  cancelled: 'var(--color-warning)',
}

function formatDuration(ms) {
  if (!ms) return ''
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rem = Math.round(s % 60)
  if (m < 60) return `${m}m ${rem}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

function formatCost(usd) {
  if (!usd) return ''
  return `$${usd.toFixed(3)}`
}

export default function PipelineVisualization({ stages, events, onStageClick }) {
  const [expandedStage, setExpandedStage] = useState(null)

  const stageEvents = (stageName) =>
    events.filter(e => e.stage === stageName).slice(-20)

  const latestProgress = (stageName) => {
    const progressEvents = events.filter(e => e.stage === stageName && e.type === 'progress')
    return progressEvents.length > 0 ? progressEvents[progressEvents.length - 1].data : null
  }

  // Get latest error for a stage
  const latestError = (stageName) => {
    const errorEvents = events.filter(e => e.stage === stageName && (e.type === 'error' || e.data?.error))
    if (errorEvents.length === 0) return null
    const last = errorEvents[errorEvents.length - 1]
    return last.data?.error || last.data?.message || null
  }

  // Calculate max duration for relative bar widths
  const maxDuration = Math.max(...stages.map(s => s.duration_ms || 0), 1)

  return (
    <div className="pipeline-viz">
      {stages.map((stage, i) => {
        const progress = latestProgress(stage.stage_name)
        const isExpanded = expandedStage === stage.stage_name
        const error = stage.status === 'failed' ? latestError(stage.stage_name) : null
        const meta = stage.metadata ? (typeof stage.metadata === 'string' ? JSON.parse(stage.metadata) : stage.metadata) : {}

        return (
          <React.Fragment key={stage.stage_name}>
            {i > 0 && (
              <div className="pipeline-connector">
                {stages[i - 1].output_count > 0 && (
                  <span className="connector-count">{stages[i - 1].output_count}</span>
                )}
              </div>
            )}
            <div
              className={`pipeline-node ${stage.status}`}
              style={{ borderColor: STATUS_COLORS[stage.status] }}
              onClick={() => {
                if (stage.status === 'completed' || stage.status === 'failed') {
                  onStageClick?.(stage.stage_name)
                }
              }}
            >
              <div className="pipeline-node-header">
                <span className={`status-dot ${stage.status}`} />
                <span className="stage-name">
                  {STAGE_LABELS[stage.stage_name] || stage.stage_name}
                </span>
                {stage.output_count > 0 && (
                  <span className="output-count">{stage.output_count}</span>
                )}
              </div>

              {/* Live elapsed timer for running stages */}
              {stage.status === 'running' && stage.started_at && (
                <div className="stage-elapsed">
                  <ElapsedTimer startTime={stage.started_at} active={true} />
                </div>
              )}

              {progress && stage.status === 'running' && (
                <div className="progress-bar-container">
                  <div
                    className="progress-bar"
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                  <span className="progress-text">{progress.message || `${progress.current}/${progress.total}`}</span>
                </div>
              )}

              {/* Timing bar for completed stages */}
              {stage.duration_ms > 0 && (
                <div className="stage-timing">
                  <div
                    className="timing-bar"
                    style={{ width: `${(stage.duration_ms / maxDuration) * 100}%` }}
                  />
                  <span className="timing-label">{formatDuration(stage.duration_ms)}</span>
                </div>
              )}

              {/* Cost */}
              {stage.cost_usd > 0 && (
                <div className="stage-cost">{formatCost(stage.cost_usd)}</div>
              )}

              {/* Inline error for failed stages */}
              {error && (
                <div className="stage-error">{error.length > 120 ? error.slice(0, 120) + '...' : error}</div>
              )}

              {/* Metadata badges */}
              {meta.degraded && (
                <span className="badge warning" style={{ fontSize: '10px', marginTop: '4px' }}>
                  degraded ({Math.round((meta.coverage_ratio || 0) * 100)}%)
                </span>
              )}
              {meta.quarantined > 0 && (
                <span className="badge warning" style={{ fontSize: '10px', marginTop: '4px' }}>
                  {meta.quarantined} quarantined
                </span>
              )}

              {(stage.status === 'completed' || stage.status === 'failed') && (
                <button
                  className="expand-btn"
                  onClick={(e) => {
                    e.stopPropagation()
                    setExpandedStage(isExpanded ? null : stage.stage_name)
                  }}
                >
                  {isExpanded ? 'Hide Logs' : 'Show Logs'}
                </button>
              )}

              {isExpanded && (
                <div className="stage-logs">
                  {stageEvents(stage.stage_name).map((evt, j) => (
                    <div key={j} className={`log-entry ${evt.type}`}>
                      <span className="log-time">
                        {new Date(evt.timestamp).toLocaleTimeString()}
                      </span>
                      <span className="log-msg">
                        {evt.data?.message || evt.data?.error || JSON.stringify(evt.data)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </React.Fragment>
        )
      })}
    </div>
  )
}
