import React, { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import useTitle from '../hooks/useTitle'

export default function ImportReview() {
  const location = useLocation()
  const navigate = useNavigate()
  const importData = location.state?.importData
  useTitle('Import Review')

  const [name, setName] = useState(importData?.name || '')
  const [type, setType] = useState('black_box')
  const [sourceRepo, setSourceRepo] = useState('')
  const [credentials, setCredentials] = useState(importData?.credentials || '')
  const [rawData, setRawData] = useState(
    JSON.stringify(importData?.raw_program_data || {}, null, 2)
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  if (!importData) {
    return (
      <div className="page">
        <div className="empty-state">
          <p>No import data. Go to <a href="/platforms">Platforms</a> to import a program.</p>
        </div>
      </div>
    )
  }

  const raw = importData.raw_program_data || {}

  // Extract display sections from raw data
  const sections = [
    { key: 'qualifying', label: 'Qualifying Vulnerabilities', data: raw.qualifying_vulnerability || raw.qualifying_vulns },
    { key: 'non_qualifying', label: 'Non-Qualifying Vulnerabilities', data: raw.non_qualifying_vulnerability || raw.non_qualifying_vulns },
    { key: 'scopes', label: 'Assets In Scope', data: raw.scopes },
    { key: 'out_of_scope', label: 'Assets Not In Scope', data: raw.out_of_scope },
    { key: 'rules', label: 'Program Rules', data: raw.rules },
    { key: 'hunter_credentials', label: 'Credentials', data: raw.hunter_credentials },
  ]

  function renderSection(data) {
    if (!data) return <span style={{ color: 'var(--text-muted)' }}>None</span>
    if (Array.isArray(data)) {
      if (data.length === 0) return <span style={{ color: 'var(--text-muted)' }}>None</span>
      return (
        <ul style={{ margin: 0, paddingLeft: 20 }}>
          {data.map((item, i) => (
            <li key={i} style={{ marginBottom: 4 }}>
              {typeof item === 'object' ? (
                <code style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{JSON.stringify(item, null, 2)}</code>
              ) : (
                String(item)
              )}
            </li>
          ))}
        </ul>
      )
    }
    if (typeof data === 'string') {
      return <pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 13 }}>{data}</pre>
    }
    return <code style={{ fontSize: 12 }}>{JSON.stringify(data, null, 2)}</code>
  }

  async function handleCreate() {
    if (!name.trim()) {
      setError('Name is required')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      // Parse edited raw data
      let parsedRaw
      try {
        parsedRaw = JSON.parse(rawData)
      } catch {
        setError('Invalid JSON in raw program data')
        setSubmitting(false)
        return
      }

      if (type === 'source_code' && !sourceRepo.trim()) {
        setError('Source repository URL is required for source code engagements')
        setSubmitting(false)
        return
      }

      const eng = await api.createEngagement({
        name: name.trim(),
        type,
        source_path: '',
        source_repo: type === 'source_code' ? sourceRepo.trim() : '',
        target_domains: importData.target_domains || [],
        scope_definition: '',
        infra_config: credentials.trim() ? `CREDENTIALS:\n${credentials.trim()}` : '',
        config_overrides: {
          raw_program_data: parsedRaw,
        },
      })
      navigate(`/engagements/${eng.id}`)
    } catch (e) {
      setError(e.message)
    }
    setSubmitting(false)
  }

  return (
    <div className="page">
      <div className="page-header">
        <h1>Import Review</h1>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24, marginBottom: 24 }}>
        <div>
          <div className="form-group">
            <label>Engagement Name</label>
            <input type="text" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Type</label>
            <select value={type} onChange={e => setType(e.target.value)}>
              <option value="black_box">Black Box</option>
              <option value="source_code">Source Code</option>
            </select>
          </div>
          {type === 'source_code' && (
            <div className="form-group">
              <label>Source Repository URL</label>
              <input type="text" value={sourceRepo} onChange={e => setSourceRepo(e.target.value)} placeholder="https://github.com/org/repo" />
            </div>
          )}
          <div className="form-group">
            <label>Credentials</label>
            <textarea value={credentials} onChange={e => setCredentials(e.target.value)} rows={3} placeholder="username / password" />
          </div>
        </div>

        <div>
          {sections.map(s => (
            s.data && (Array.isArray(s.data) ? s.data.length > 0 : true) ? (
              <div key={s.key} style={{ marginBottom: 16 }}>
                <h3 style={{ fontSize: 14, marginBottom: 6 }}>{s.label}</h3>
                <div style={{
                  background: 'var(--bg-primary)', border: '1px solid var(--border)',
                  borderRadius: 6, padding: '8px 12px', maxHeight: 200, overflow: 'auto', fontSize: 13,
                }}>
                  {renderSection(s.data)}
                </div>
              </div>
            ) : null
          ))}
        </div>
      </div>

      <details style={{ marginBottom: 24 }}>
        <summary style={{ cursor: 'pointer', fontSize: 14, fontWeight: 500, marginBottom: 8 }}>
          Raw Program Data (editable)
        </summary>
        <textarea
          value={rawData}
          onChange={e => setRawData(e.target.value)}
          rows={20}
          style={{ width: '100%', fontFamily: 'monospace', fontSize: 12, background: 'var(--bg-primary)', color: 'var(--text-primary)', border: '1px solid var(--border)', borderRadius: 6, padding: 12 }}
        />
      </details>

      {error && <div className="error-msg" style={{ marginBottom: 12 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 8 }}>
        <button className="btn btn-primary" onClick={handleCreate} disabled={submitting}>
          {submitting ? 'Creating...' : 'Create Engagement'}
        </button>
        <button className="btn btn-secondary" onClick={() => navigate('/platforms')}>Back</button>
      </div>
    </div>
  )
}
