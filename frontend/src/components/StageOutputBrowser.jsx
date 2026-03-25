import React, { useState, useEffect } from 'react'
import { api } from '../utils/api'
import ConversationView from './ConversationView'

function FolderIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round">
      <path d="M2 4.5V12a1 1 0 001 1h10a1 1 0 001-1V6a1 1 0 00-1-1H8L6.5 3.5H3A1 1 0 002 4.5z" />
    </svg>
  )
}

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round">
      <path d="M4 1.5h5.5L13 5v9a1 1 0 01-1 1H4a1 1 0 01-1-1V2.5a1 1 0 011-1z" />
      <path d="M9.5 1.5V5H13" />
    </svg>
  )
}

function ChatIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinejoin="round">
      <path d="M2 3h12a1 1 0 011 1v7a1 1 0 01-1 1H5l-3 2.5V4a1 1 0 011-1z" />
      <path d="M5 7h6M5 9.5h3" strokeLinecap="round" />
    </svg>
  )
}

function UpIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 12V4M4.5 7.5L8 4l3.5 3.5" />
    </svg>
  )
}

/** Render structured JSON content in a human-readable format */
function SmartContent({ data, filename }) {
  if (!data) return null

  // Bug list (all_findings.json, validated_bugs.json, confirmed_bugs.json, etc.)
  if (Array.isArray(data) && data.length > 0 && data[0]?.vuln_type) {
    return <BugList bugs={data} />
  }

  // Bug list with different key names
  if (Array.isArray(data) && data.length > 0 && (data[0]?.vuln_class || data[0]?.vulnerability_type)) {
    return <BugList bugs={data} />
  }

  // Attack surfaces
  if (Array.isArray(data) && data.length > 0 && data[0]?.attack_surfaces) {
    return <AttackSurfaceList surfaces={data[0].attack_surfaces} />
  }
  if (Array.isArray(data) && data.length > 0 && data[0]?.name && data[0]?.status) {
    return <AttackSurfaceList surfaces={data} />
  }

  // Scope data
  if (data?.attack_surfaces && data?.architecture) {
    return <ScopeView data={data} />
  }

  // Duplicate groups
  if (Array.isArray(data) && data.length > 0 && data[0]?.merged_into) {
    return <DuplicateGroups groups={data} />
  }

  // Chain data
  if (Array.isArray(data) && data.length > 0 && data[0]?.bug_ids) {
    return <ChainList chains={data} />
  }

  // Setup/tool check
  if (data?.tools && Array.isArray(data.tools)) {
    return <ToolReport data={data} />
  }

  // Fallback: formatted JSON with toggle
  return <FormattedJson data={data} />
}

function BugList({ bugs }) {
  const [expanded, setExpanded] = useState(null)
  return (
    <div className="smart-content">
      <div className="smart-header">{bugs.length} finding{bugs.length !== 1 ? 's' : ''}</div>
      {bugs.map((bug, i) => {
        const isOpen = expanded === i
        return (
          <div key={i} className={`smart-bug ${bug.severity || 'unknown'}`}>
            <div className="smart-bug-header" onClick={() => setExpanded(isOpen ? null : i)}>
              {bug.severity && <span className={`severity-badge ${bug.severity}`}>{bug.severity}</span>}
              <span className="smart-bug-id">{bug.id || `#${i}`}</span>
              <span className="smart-bug-type">{bug.vuln_type || bug.vulnerability_type || bug.vuln_class || ''}</span>
              <span className="smart-bug-loc">
                {bug.source_file ? `${bug.source_file}${bug.line_range ? ':' + bug.line_range : ''}` : bug.url || ''}
              </span>
              <span className="expand-indicator">{isOpen ? '-' : '+'}</span>
            </div>
            {isOpen && (
              <div className="smart-bug-body">
                {bug.description && <Field label="Description" value={bug.description} />}
                {bug.root_cause && <Field label="Root Cause" value={bug.root_cause} />}
                {bug.reasoning && <Field label="Reasoning" value={bug.reasoning} />}
                {bug.security_impact && <Field label="Security Impact" value={bug.security_impact} />}
                {bug.confidence && <Field label="Confidence" value={bug.confidence} />}
                {bug.found_by && <Field label="Found by" value={Array.isArray(bug.found_by) ? bug.found_by.join(', ') : bug.found_by} />}
                {bug.validated !== undefined && <Field label="Validated" value={bug.validated ? 'Yes' : 'No'} />}
                {bug.cannot_validate_reason && <Field label="Cannot Validate" value={bug.cannot_validate_reason} />}
                {bug.triager_notes && <Field label="Triager Notes" value={bug.triager_notes} />}
                {bug.scope_reasoning && <Field label="Scope Reasoning" value={bug.scope_reasoning} />}
                {bug.poc && <PocView poc={bug.poc} />}
                {bug.expanded_primitives && <ExpansionView data={bug.expanded_primitives} />}
                {bug.http_evidence && <HttpEvidence evidence={bug.http_evidence} />}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function PocView({ poc }) {
  if (!poc) return null
  return (
    <div className="smart-poc">
      <div className="smart-poc-header">
        <strong>PoC</strong>
        {poc.language && <span className="poc-lang">{poc.language}</span>}
        {poc.execution_result && (
          <span className={`badge ${poc.execution_result === 'success' ? 'success' : 'failed'}`}>
            {poc.execution_result}
          </span>
        )}
      </div>
      {poc.code && <pre className="poc-code">{poc.code}</pre>}
      {poc.output && (
        <div className="poc-output-section">
          <strong>Output:</strong>
          <pre className="poc-output">{poc.output}</pre>
        </div>
      )}
      {poc.file && <div className="poc-file">File: {poc.file}</div>}
    </div>
  )
}

function ExpansionView({ data }) {
  if (!data) return null
  return (
    <div className="smart-expansion">
      <strong>Expanded Primitives:</strong>
      {data.demonstrated?.map((exp, i) => (
        <div key={i} className="expansion-item demonstrated">
          <span className="badge success">Demonstrated</span>
          <span>{exp.primitive}</span>
        </div>
      ))}
      {data.theoretical?.map((exp, i) => (
        <div key={i} className="expansion-item theoretical">
          <span className="badge warning">Theoretical</span>
          <span>{exp.primitive}</span>
          {exp.reason_not_demonstrated && (
            <span className="muted"> — {exp.reason_not_demonstrated}</span>
          )}
        </div>
      ))}
    </div>
  )
}

function HttpEvidence({ evidence }) {
  if (!evidence) return null
  return (
    <div className="smart-http">
      <strong>HTTP Evidence:</strong>
      {evidence.request && (
        <div>
          <div className="http-label">Request:</div>
          <pre className="http-content">{evidence.request}</pre>
        </div>
      )}
      {evidence.response && (
        <div>
          <div className="http-label">Response:</div>
          <pre className="http-content">{evidence.response}</pre>
        </div>
      )}
    </div>
  )
}

function AttackSurfaceList({ surfaces }) {
  return (
    <div className="smart-content">
      <div className="smart-header">{surfaces.length} attack surface{surfaces.length !== 1 ? 's' : ''}</div>
      {surfaces.map((s, i) => (
        <div key={i} className={`smart-surface ${s.status || ''}`}>
          <span className={`badge ${s.status === 'scanned' ? 'success' : 'pending'}`}>{s.status || 'unknown'}</span>
          <strong>{s.name || s.id}</strong>
          {s.priority && <span className={`severity-badge ${s.priority}`}>{s.priority}</span>}
          {s.description && <div className="muted" style={{ marginTop: '4px' }}>{s.description}</div>}
          {s.findings_notes && <div style={{ marginTop: '4px', fontSize: '12px' }}>{s.findings_notes}</div>}
        </div>
      ))}
    </div>
  )
}

function ScopeView({ data }) {
  const arch = data.architecture || {}
  return (
    <div className="smart-content">
      <div className="smart-header">Scope Analysis</div>
      <div className="smart-section">
        <h4>Architecture</h4>
        {arch.description && <p>{arch.description}</p>}
        {arch.framework && <Field label="Framework" value={arch.framework} />}
        {arch.entry_points && <Field label="Entry Points" value={arch.entry_points.join(', ')} />}
      </div>
      {data.attack_surfaces && <AttackSurfaceList surfaces={data.attack_surfaces} />}
      {data.scope_notes && (
        <div className="smart-section">
          <h4>Scope Notes</h4>
          {data.scope_notes.qualifying?.length > 0 && (
            <Field label="Qualifying" value={data.scope_notes.qualifying.join(', ')} />
          )}
          {data.scope_notes.non_qualifying?.length > 0 && (
            <Field label="Non-qualifying" value={data.scope_notes.non_qualifying.join(', ')} />
          )}
        </div>
      )}
    </div>
  )
}

function ChainList({ chains }) {
  return (
    <div className="smart-content">
      <div className="smart-header">{chains.length} chain{chains.length !== 1 ? 's' : ''}</div>
      {chains.map((c, i) => (
        <div key={i} className="smart-chain">
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <span className={`badge ${c.status}`}>{c.status}</span>
            {c.severity && <span className={`severity-badge ${c.severity}`}>{c.severity}</span>}
            <strong>{c.id}</strong>
          </div>
          <div>{c.description}</div>
          {c.bug_ids && <Field label="Bugs" value={c.bug_ids.join(' + ')} />}
          {c.combined_impact && <Field label="Combined Impact" value={c.combined_impact} />}
          {c.execution_order && <pre className="poc-code">{c.execution_order}</pre>}
        </div>
      ))}
    </div>
  )
}

function DuplicateGroups({ groups }) {
  return (
    <div className="smart-content">
      <div className="smart-header">{groups.length} duplicate group{groups.length !== 1 ? 's' : ''}</div>
      {groups.map((g, i) => (
        <div key={i} className="smart-dedup">
          <Field label="Merged into" value={g.merged_into} />
          <Field label="Duplicates" value={(g.duplicates || []).join(', ')} />
          {g.reason && <Field label="Reason" value={g.reason} />}
        </div>
      ))}
    </div>
  )
}

function ToolReport({ data }) {
  return (
    <div className="smart-content">
      <div className="smart-header">
        Tool Check — {data.all_required_available ? 'All required tools available' : 'Missing required tools'}
      </div>
      {data.tools.map((t, i) => (
        <div key={i} className="smart-tool">
          <span className={`badge ${t.available ? 'success' : t.required ? 'failed' : 'pending'}`}>
            {t.available ? 'OK' : 'Missing'}
          </span>
          <strong>{t.name}</strong>
          <span className="muted">{t.description}</span>
          {t.path && <span className="muted" style={{ fontSize: '11px' }}>{t.path}</span>}
        </div>
      ))}
    </div>
  )
}

function Field({ label, value }) {
  return (
    <div className="smart-field">
      <span className="smart-field-label">{label}:</span>
      <span className="smart-field-value">{value}</span>
    </div>
  )
}

function FormattedJson({ data }) {
  const [raw, setRaw] = useState(false)
  const text = JSON.stringify(data, null, 2)
  return (
    <div className="smart-content">
      <div className="smart-header" style={{ display: 'flex', justifyContent: 'space-between' }}>
        <span>JSON Data</span>
        <button className="btn btn-sm" onClick={() => setRaw(!raw)}>{raw ? 'Formatted' : 'Raw'}</button>
      </div>
      <pre className="file-content-pre">{text}</pre>
    </div>
  )
}


export default function StageOutputBrowser({ engagementId, runId, stageName, onClose }) {
  const [files, setFiles] = useState([])
  const [content, setContent] = useState(null)
  const [currentPath, setCurrentPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [conversationPath, setConversationPath] = useState(null)

  useEffect(() => {
    loadDirectory('')
  }, [engagementId, runId, stageName])

  async function loadDirectory(path) {
    setLoading(true)
    setError(null)
    try {
      const data = await api.getStageOutput(engagementId, runId, stageName, path)
      if (data.files) {
        setFiles(data.files)
        setContent(null)
        setCurrentPath(path)
      } else if (data.content !== undefined) {
        setContent(data)
        setCurrentPath(path)
        setFiles([])
      }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  function navigateUp() {
    const parts = currentPath.split('/')
    parts.pop()
    loadDirectory(parts.join('/'))
  }

  const filename = currentPath.split('/').pop() || ''

  return (
    <div className="stage-output-browser">
      <div className="browser-header">
        <h3>{stageName}</h3>
        <span className="browser-path">/{currentPath}</span>
        <button className="close-btn" onClick={onClose}>Close</button>
      </div>

      {error && <div className="error-msg">{error}</div>}
      {loading && <div className="loading">Loading...</div>}

      {currentPath && !content && (
        <div className="file-item" onClick={navigateUp}>
          <span className="file-icon"><UpIcon /></span>
          <span className="file-name">..</span>
        </div>
      )}

      {files.map(file => (
        <div
          key={file.path}
          className="file-item"
          onClick={() => {
            if (file.name.endsWith('.jsonl')) {
              setConversationPath(file.path)
            } else {
              loadDirectory(file.path)
            }
          }}
        >
          <span className="file-icon">
            {file.is_dir ? <FolderIcon /> : file.name.endsWith('.jsonl') ? <ChatIcon /> : <FileIcon />}
          </span>
          <span className="file-name">{file.name}</span>
          {file.name.endsWith('.jsonl') && <span className="file-tag">conversation</span>}
          {file.size !== undefined && (
            <span className="file-size">{formatSize(file.size)}</span>
          )}
        </div>
      ))}

      {conversationPath && (
        <ConversationView
          engagementId={engagementId}
          runId={runId}
          stageName={stageName}
          jsonlPath={conversationPath}
          onClose={() => setConversationPath(null)}
        />
      )}

      {content && !conversationPath && (
        <div className="file-content">
          {content.type === 'json' ? (
            <SmartContent data={content.content} filename={filename} />
          ) : (
            <pre className="file-content-pre">{content.content}</pre>
          )}
        </div>
      )}
    </div>
  )
}

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`
}
