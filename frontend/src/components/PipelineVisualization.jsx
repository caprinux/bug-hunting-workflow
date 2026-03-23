import React, { useState } from 'react'

const STAGE_LABELS = {
  setup: 'Setup',
  workload_divider: 'Workload Divider',
  scope_enumerator: 'Scope Enumerator',
  bug_hunter: 'Bug Hunter',
  deduplicator: 'De-duplicator',
  scope_validator: 'Scope Validator',
  strict_validator: 'Strict Validator',
  perfectionist: 'Perfectionist',
  strict_triager: 'Strict Triager',
  bug_chainer: 'Bug Chainer',
}

const STATUS_COLORS = {
  pending: 'var(--color-muted)',
  running: 'var(--color-info)',
  completed: 'var(--color-success)',
  failed: 'var(--color-error)',
  skipped: 'var(--color-muted)',
}

export default function PipelineVisualization({ stages, events, onStageClick }) {
  const [expandedStage, setExpandedStage] = useState(null)

  const stageEvents = (stageName) =>
    events.filter(e => e.stage === stageName).slice(-20)

  const latestProgress = (stageName) => {
    const progressEvents = events.filter(e => e.stage === stageName && e.type === 'progress')
    return progressEvents.length > 0 ? progressEvents[progressEvents.length - 1].data : null
  }

  return (
    <div className="pipeline-viz">
      {stages.map((stage, i) => {
        const progress = latestProgress(stage.stage_name)
        const isExpanded = expandedStage === stage.stage_name

        return (
          <React.Fragment key={stage.stage_name}>
            {i > 0 && <div className="pipeline-connector" />}
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

              {progress && stage.status === 'running' && (
                <div className="progress-bar-container">
                  <div
                    className="progress-bar"
                    style={{ width: `${(progress.current / progress.total) * 100}%` }}
                  />
                  <span className="progress-text">{progress.message || `${progress.current}/${progress.total}`}</span>
                </div>
              )}

              {stage.duration_ms > 0 && (
                <div className="stage-duration">{(stage.duration_ms / 1000).toFixed(1)}s</div>
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
