import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../utils/api'

const DEFAULT_ADVANCED = {
  agents: ['claude', 'codex'],
  retry_limit: 3,
  subagent_timeout: 3600,
  max_concurrent_infra_agents: 5,
  request_delay: 0,
  destructive_poc_policy: 'cannot_validate',
  contrived_threshold: 3,
  severity_floor: 'low',
}

export default function NewEngagement() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    name: '',
    type: 'source_code',
    source_path: '',
    source_repo: '',
    target_domains: '',
    qualifying_vulns: '',
    non_qualifying_vulns: '',
    assets_in_scope: '',
    assets_not_in_scope: '',
    scope_notes: '',
    credentials: '',
    infra_url: '',
  })
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [advanced, setAdvanced] = useState(DEFAULT_ADVANCED)
  const [advancedDirty, setAdvancedDirty] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getSettings()
      .then(settings => {
        setAdvanced({
          agents: settings?.bug_hunter?.agents?.length ? settings.bug_hunter.agents : DEFAULT_ADVANCED.agents,
          retry_limit: settings?.pipeline?.retry_limit ?? DEFAULT_ADVANCED.retry_limit,
          subagent_timeout: settings?.pipeline?.subagent_timeout ?? DEFAULT_ADVANCED.subagent_timeout,
          max_concurrent_infra_agents: settings?.pipeline?.max_concurrent_infra_agents ?? DEFAULT_ADVANCED.max_concurrent_infra_agents,
          request_delay: settings?.pipeline?.request_delay ?? DEFAULT_ADVANCED.request_delay,
          destructive_poc_policy: settings?.strict_validator?.destructive_poc_policy ?? DEFAULT_ADVANCED.destructive_poc_policy,
          contrived_threshold: settings?.strict_triager?.contrived_threshold ?? DEFAULT_ADVANCED.contrived_threshold,
          severity_floor: settings?.strict_triager?.severity_floor ?? DEFAULT_ADVANCED.severity_floor,
        })
      })
      .catch(() => {})
  }, [])

  async function handleSubmit(e) {
    e.preventDefault()
    setSubmitting(true)
    setError(null)

    try {
      const config_overrides = advancedDirty ? {
        pipeline: {
          retry_limit: advanced.retry_limit,
          subagent_timeout: advanced.subagent_timeout,
          max_concurrent_infra_agents: advanced.max_concurrent_infra_agents,
          request_delay: advanced.request_delay,
        },
        bug_hunter: {
          agents: advanced.agents,
        },
        strict_validator: { destructive_poc_policy: advanced.destructive_poc_policy },
        strict_triager: {
          contrived_threshold: advanced.contrived_threshold,
          severity_floor: advanced.severity_floor,
        },
      } : {}

      // Compose scope_definition from structured fields
      const scopeParts = []
      if (form.qualifying_vulns.trim())
        scopeParts.push(`QUALIFYING VULNERABILITIES:\n${form.qualifying_vulns.trim()}`)
      if (form.non_qualifying_vulns.trim())
        scopeParts.push(`NON-QUALIFYING VULNERABILITIES:\n${form.non_qualifying_vulns.trim()}`)
      if (form.assets_in_scope.trim())
        scopeParts.push(`ASSETS IN SCOPE:\n${form.assets_in_scope.trim()}`)
      if (form.assets_not_in_scope.trim())
        scopeParts.push(`ASSETS NOT IN SCOPE:\n${form.assets_not_in_scope.trim()}`)
      if (form.scope_notes.trim())
        scopeParts.push(`ADDITIONAL NOTES:\n${form.scope_notes.trim()}`)

      // Compose infra_config from structured fields
      const infraParts = []
      if (form.infra_url.trim())
        infraParts.push(`TARGET URL: ${form.infra_url.trim()}`)
      if (form.credentials.trim())
        infraParts.push(`CREDENTIALS:\n${form.credentials.trim()}`)

      const data = {
        name: form.name,
        type: form.type,
        source_path: form.type === 'source_code' ? form.source_path : '',
        source_repo: form.type === 'source_code' ? form.source_repo : '',
        target_domains: form.type === 'black_box'
          ? form.target_domains.split('\n').map(d => d.trim()).filter(Boolean)
          : [],
        scope_definition: scopeParts.join('\n\n'),
        infra_config: infraParts.join('\n\n'),
        config_overrides,
      }

      const eng = await api.createEngagement(data)
      navigate(`/engagements/${eng.id}`)
    } catch (e) {
      setError(e.message)
    }
    setSubmitting(false)
  }

  const update = (field, value) => setForm(f => ({ ...f, [field]: value }))
  const updateAdv = (field, value) => {
    setAdvancedDirty(true)
    setAdvanced(a => ({ ...a, [field]: value }))
  }

  return (
    <div className="page new-engagement">
      <h1>New Engagement</h1>

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Engagement Name</label>
          <input type="text" value={form.name} onChange={e => update('name', e.target.value)}
                 placeholder="e.g., Audit Target X" required />
        </div>

        <div className="form-group">
          <label>Engagement Type</label>
          <div className="type-selector">
            <button type="button" className={`type-btn ${form.type === 'source_code' ? 'active' : ''}`}
                    onClick={() => update('type', 'source_code')}>
              Source Code Audit
            </button>
            <button type="button" className={`type-btn ${form.type === 'black_box' ? 'active' : ''}`}
                    onClick={() => update('type', 'black_box')}>
              Black Box Pentest
            </button>
          </div>
        </div>

        {form.type === 'source_code' && (
          <>
            <div className="form-group">
              <label>Local Source Path</label>
              <input type="text" value={form.source_path} onChange={e => update('source_path', e.target.value)}
                     placeholder="/path/to/source/code" />
            </div>
            <div className="form-group">
              <label>Or GitHub Repo URL(s)</label>
              <input type="text" value={form.source_repo} onChange={e => update('source_repo', e.target.value)}
                     placeholder="https://github.com/user/repo — separate multiple with commas" />
              <small style={{color: 'var(--text-muted)', fontSize: '12px'}}>
                Supports branch/commit: repo@branch or repo#commit. Multiple repos: repo1, repo2
              </small>
            </div>
          </>
        )}

        {form.type === 'black_box' && (
          <div className="form-group">
            <label>Target Domains (one per line)</label>
            <textarea value={form.target_domains} onChange={e => update('target_domains', e.target.value)}
                      placeholder={"*.example.com\napi.example.com\nadmin.example.com"} rows={4} />
          </div>
        )}

        <h2 style={{ fontSize: '16px', marginTop: '24px', marginBottom: '12px' }}>Scope</h2>

        <div className="form-group">
          <label>Assets In Scope</label>
          <textarea value={form.assets_in_scope} onChange={e => update('assets_in_scope', e.target.value)}
                    placeholder={"e.g.:\n- All code in api/src/\n- https://backend.staging.example.com\n- Mobile API endpoints (/api/v1/*)"}
                    rows={3} />
        </div>

        <div className="form-group">
          <label>Assets Not In Scope <span className="muted">(optional)</span></label>
          <textarea value={form.assets_not_in_scope} onChange={e => update('assets_not_in_scope', e.target.value)}
                    placeholder={"e.g.:\n- Third-party dependencies\n- Infrastructure/hosting\n- Marketing website"}
                    rows={3} />
        </div>

        <div className="form-group">
          <label>Qualifying Vulnerabilities</label>
          <textarea value={form.qualifying_vulns} onChange={e => update('qualifying_vulns', e.target.value)}
                    placeholder={"e.g.:\n- Remote Code Execution\n- SQL Injection\n- Authentication Bypass\n- IDOR / Broken Access Control\n- SSRF\n- Stored XSS"}
                    rows={4} />
        </div>

        <div className="form-group">
          <label>Non-Qualifying Vulnerabilities</label>
          <textarea value={form.non_qualifying_vulns} onChange={e => update('non_qualifying_vulns', e.target.value)}
                    placeholder={"e.g.:\n- Self-XSS\n- Missing security headers without exploit\n- Rate limiting\n- Clickjacking on non-sensitive pages\n- Version disclosure"}
                    rows={4} />
        </div>

        <div className="form-group">
          <label>Additional Scope Notes <span className="muted">(optional)</span></label>
          <textarea value={form.scope_notes} onChange={e => update('scope_notes', e.target.value)}
                    placeholder="Any other rules, special conditions, or context for the engagement..."
                    rows={2} />
        </div>

        <h2 style={{ fontSize: '16px', marginTop: '24px', marginBottom: '12px' }}>Infrastructure</h2>

        <div className="form-group">
          <label>Target URL <span className="muted">(optional)</span></label>
          <input type="text" value={form.infra_url} onChange={e => update('infra_url', e.target.value)}
                 placeholder="e.g., https://backend.staging.example.com" />
        </div>

        <div className="form-group">
          <label>Credentials <span className="muted">(optional)</span></label>
          <textarea value={form.credentials} onChange={e => update('credentials', e.target.value)}
                    placeholder={"e.g.:\nuser: test@example.com / password123\nadmin: admin@example.com / admin456\nAPI key: Bearer sk-abc123..."}
                    rows={4} />
        </div>

        <div className="form-group">
          <button type="button" className="btn btn-secondary" onClick={() => setShowAdvanced(!showAdvanced)}>
            {showAdvanced ? 'Hide' : 'Show'} Advanced Configuration
          </button>
        </div>

        {showAdvanced && (
          <div className="advanced-config">
            <div className="config-grid">
              <div className="form-group">
                <label>Retry Limit</label>
                <input type="number" value={advanced.retry_limit}
                       onChange={e => updateAdv('retry_limit', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <label>Bug Hunter Agents</label>
                <div className="agent-checkboxes">
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={advanced.agents.includes('claude')}
                      onChange={e => {
                        const next = e.target.checked
                          ? [...new Set([...advanced.agents, 'claude'])]
                          : advanced.agents.filter(a => a !== 'claude')
                        if (next.length > 0) updateAdv('agents', next)
                      }}
                    />
                    <span>Claude</span>
                  </label>
                  <label className="toggle-label">
                    <input
                      type="checkbox"
                      checked={advanced.agents.includes('codex')}
                      onChange={e => {
                        const next = e.target.checked
                          ? [...new Set([...advanced.agents, 'codex'])]
                          : advanced.agents.filter(a => a !== 'codex')
                        if (next.length > 0) updateAdv('agents', next)
                      }}
                    />
                    <span>Codex</span>
                  </label>
                </div>
              </div>
              <div className="form-group">
                <label>Subagent Timeout (s)</label>
                <input type="number" value={advanced.subagent_timeout}
                       onChange={e => updateAdv('subagent_timeout', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <label>Max Concurrent Infra Agents</label>
                <input type="number" value={advanced.max_concurrent_infra_agents}
                       onChange={e => updateAdv('max_concurrent_infra_agents', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <label>Request Delay (s)</label>
                <input type="number" step="0.1" value={advanced.request_delay}
                       onChange={e => updateAdv('request_delay', parseFloat(e.target.value))} />
              </div>
              <div className="form-group">
                <label>Contrived Threshold</label>
                <input type="number" value={advanced.contrived_threshold}
                       onChange={e => updateAdv('contrived_threshold', parseInt(e.target.value))} />
              </div>
              <div className="form-group">
                <label>Severity Floor</label>
                <select value={advanced.severity_floor}
                        onChange={e => updateAdv('severity_floor', e.target.value)}>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </select>
              </div>
              <div className="form-group">
                <label>Destructive PoC Policy</label>
                <select value={advanced.destructive_poc_policy}
                        onChange={e => updateAdv('destructive_poc_policy', e.target.value)}>
                  <option value="cannot_validate">Cannot Validate (safe)</option>
                  <option value="allow">Allow (disposable infra)</option>
                </select>
              </div>
            </div>
          </div>
        )}

        {error && <div className="error-msg">{error}</div>}

        <button type="submit" className="btn btn-primary btn-large" disabled={submitting}>
          {submitting ? 'Creating...' : 'Create Engagement'}
        </button>
      </form>
    </div>
  )
}
