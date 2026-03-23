import React, { useState, useEffect } from 'react'
import { api } from '../utils/api'

const CLAUDE_MODEL_OPTIONS = [
  { value: 'opus', label: 'Claude Opus 4.6' },
  { value: 'sonnet', label: 'Claude Sonnet 4.6' },
  { value: 'haiku', label: 'Claude Haiku 4.5' },
]

const CODEX_MODEL_OPTIONS = [
  { value: 'gpt-5.4', label: 'GPT-5.4' },
  { value: 'o3', label: 'GPT o3' },
  { value: 'o4-mini', label: 'GPT o4-mini' },
  { value: 'gpt-4.1', label: 'GPT-4.1' },
]

const SECTIONS = [
  {
    key: 'pipeline',
    label: 'Pipeline',
    description: 'Global pipeline behavior — retries, timeouts, concurrency.',
    fields: [
      { key: 'retry_limit', label: 'Retry Limit', type: 'number', help: 'Retries per subagent before logging failure' },
      { key: 'subagent_timeout', label: 'Subagent Timeout (s)', type: 'number', help: 'Max seconds per subagent before kill' },
      { key: 'max_concurrent_infra_agents', label: 'Max Concurrent Infra Agents', type: 'number', help: 'Parallel agents hitting live infrastructure' },
      { key: 'request_delay', label: 'Request Delay (s)', type: 'float', help: 'Delay between requests to target (black box)' },
      { key: 'verbose', label: 'Verbose Logging', type: 'bool', help: 'Detailed logging at each stage' },
      { key: 'resume', label: 'Resume on Restart', type: 'bool', help: 'Resume pipeline from last checkpoint on restart' },
      { key: 'auto_install_tools', label: 'Auto-install Tools', type: 'bool', help: 'Automatically install missing tools at setup' },
    ],
  },
  {
    key: 'broad_bug_hunter',
    label: 'Bug Hunter',
    description: 'Controls for the Broad Bug Hunter stage.',
    fields: [
      { key: 'agents', label: 'Agents', type: 'agent_checkboxes', help: 'Which AI agents to run concurrently during bug hunting' },
      { key: 'codex_model', label: 'Codex Model', type: 'select', options: CODEX_MODEL_OPTIONS, help: 'Model used by Codex CLI (only applies when Codex is enabled)' },
      { key: 'context_budget', label: 'Context Budget (tokens)', type: 'number', help: 'Max tokens per subagent code chunk' },
      { key: 'phase2_enabled', label: 'Phase 2 (Logic Bugs)', type: 'bool', help: 'Enable cross-component logic bug hunting' },
      { key: 'exclude_paths', label: 'Exclude Paths', type: 'tags', help: 'Directories to skip (e.g. node_modules, vendor)' },
      { key: 'file_extensions', label: 'File Extensions', type: 'tags', help: 'Limit to specific extensions (empty = all)' },
    ],
  },
  {
    key: 'workload_divider',
    label: 'Workload Divider',
    description: 'For massive codebases — splits into independent subsystems.',
    fields: [
      { key: 'enabled', label: 'Enabled', type: 'bool', help: 'Enable workload splitting for large codebases' },
      { key: 'subsystem_strategy', label: 'Strategy', type: 'select', options: ['auto', 'manual'], help: 'Auto-detect subsystems or use manual list' },
    ],
  },
  {
    key: 'scope_enumerator',
    label: 'Scope Enumerator',
    description: 'Reconnaissance settings for black box pentesting.',
    fields: [
      { key: 'recon_mode', label: 'Recon Mode', type: 'select', options: ['both', 'active', 'passive'], help: 'Active, passive, or both' },
    ],
  },
  {
    key: 'black_box_bug_hunter',
    label: 'Black Box Bug Hunter',
    description: 'Settings for black box per-target bug hunting.',
    fields: [
      { key: 'checkpoint_context_threshold', label: 'Checkpoint Threshold', type: 'float', help: 'Checkpoint at this context usage ratio (0.0-1.0)' },
    ],
  },
  {
    key: 'deduplicator',
    label: 'De-duplicator',
    description: 'Merge duplicate findings from multiple agents.',
    fields: [
      { key: 'enabled', label: 'Force Enable', type: 'bool', help: 'Always enable (auto-enabled when multiple agents run)' },
      { key: 'similarity_threshold', label: 'Similarity Threshold', type: 'float', help: 'How similar findings must be to merge (0.0-1.0)' },
    ],
  },
  {
    key: 'strict_validator',
    label: 'Strict Validator',
    description: 'PoC execution and exploitability validation.',
    fields: [
      { key: 'destructive_poc_policy', label: 'Destructive PoC Policy', type: 'select', options: ['cannot_validate', 'allow'], help: 'How to handle PoCs that would damage the target' },
      { key: 'max_concurrent', label: 'Max Concurrent', type: 'number', help: 'Parallel PoC executions against infra' },
      { key: 'poc_language', label: 'Default PoC Language', type: 'text', help: 'Default language for PoCs (agent may override)' },
    ],
  },
  {
    key: 'perfectionist',
    label: 'Perfectionist',
    description: 'Single-bug primitive expansion.',
    fields: [
      { key: 'max_concurrent', label: 'Max Concurrent', type: 'number', help: 'Parallel expansion attempts against infra' },
    ],
  },
  {
    key: 'strict_triager',
    label: 'Strict Triager',
    description: 'Final quality gate — filters noise.',
    fields: [
      { key: 'contrived_threshold', label: 'Contrived Threshold', type: 'number', help: 'Max improbable preconditions before a bug is considered contrived' },
      { key: 'severity_floor', label: 'Severity Floor', type: 'select', options: ['low', 'medium', 'high', 'critical'], help: 'Minimum severity to survive triage' },
    ],
  },
  {
    key: 'bug_chainer',
    label: 'Bug Chainer',
    description: 'Cross-bug analysis and chain construction.',
    fields: [
      { key: 'max_concurrent', label: 'Max Concurrent', type: 'number', help: 'Parallel chain PoC executions' },
    ],
  },
  {
    key: 'models',
    label: 'Models',
    description: 'LLM model selection per pipeline stage.',
    fields: [
      { key: 'workload_divider', label: 'Workload Divider', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'bug_hunter_orchestrator', label: 'Bug Hunter Orchestrator', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'bug_hunter_subagent', label: 'Bug Hunter Subagent', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'scope_enumerator', label: 'Scope Enumerator', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'black_box_bug_hunter', label: 'Black Box Bug Hunter', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'deduplicator', label: 'De-duplicator', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'scope_validator', label: 'Scope Validator', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'strict_validator', label: 'Strict Validator', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'perfectionist', label: 'Perfectionist', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'strict_triager', label: 'Strict Triager', type: 'select', options: CLAUDE_MODEL_OPTIONS },
      { key: 'bug_chainer', label: 'Bug Chainer', type: 'select', options: CLAUDE_MODEL_OPTIONS },
    ],
  },
]

export default function Settings() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getSettings()
      .then(setSettings)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  function updateField(section, key, value) {
    setSettings(prev => ({
      ...prev,
      [section]: { ...prev[section], [key]: value },
    }))
    setSaved(false)
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    setSaved(false)
    try {
      const updated = await api.updateSettings(settings)
      setSettings(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (e) {
      setError(e.message)
    }
    setSaving(false)
  }

  function renderField(section, field) {
    const value = settings?.[section]?.[field.key]
    const onChange = (v) => updateField(section, field.key, v)

    switch (field.type) {
      case 'number':
        return (
          <input type="number" value={value ?? ''} onChange={e => onChange(parseInt(e.target.value) || 0)} />
        )
      case 'float':
        return (
          <input type="number" step="0.1" value={value ?? ''} onChange={e => onChange(parseFloat(e.target.value) || 0)} />
        )
      case 'bool':
        return (
          <label className="toggle-label">
            <input type="checkbox" checked={!!value} onChange={e => onChange(e.target.checked)} />
            <span>{value ? 'Enabled' : 'Disabled'}</span>
          </label>
        )
      case 'select':
        return (
          <select value={value ?? ''} onChange={e => onChange(e.target.value)}>
            {field.options.map(opt => {
              const optValue = typeof opt === 'object' ? opt.value : opt
              const optLabel = typeof opt === 'object' ? opt.label : opt
              return <option key={optValue} value={optValue}>{optLabel}</option>
            })}
          </select>
        )
      case 'agent_checkboxes':
        return (
          <div className="agent-checkboxes">
            <label className="toggle-label">
              <input type="checkbox" checked={true} disabled />
              <span>Claude (Claude Opus 4.6)</span>
            </label>
            <label className="toggle-label">
              <input
                type="checkbox"
                checked={(value || []).includes('codex')}
                onChange={e => {
                  if (e.target.checked) {
                    onChange([...new Set([...(value || ['claude']), 'codex'])])
                  } else {
                    onChange((value || []).filter(a => a !== 'codex'))
                  }
                }}
              />
              <span>Codex (OpenAI)</span>
            </label>
          </div>
        )
      case 'text':
        return (
          <input type="text" value={value ?? ''} onChange={e => onChange(e.target.value)} />
        )
      case 'tags':
        return (
          <TagsInput value={value || []} onChange={onChange} />
        )
      default:
        return <input type="text" value={value ?? ''} onChange={e => onChange(e.target.value)} />
    }
  }

  if (loading) return <div className="loading">Loading settings...</div>

  return (
    <div className="page settings-page">
      <div className="page-header">
        <h1>Settings</h1>
        <div className="header-actions">
          {saved && <span style={{ color: 'var(--color-success)', fontSize: '14px' }}>Saved</span>}
          {error && <span className="error-msg">{error}</span>}
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      <p style={{ color: 'var(--text-secondary)', marginBottom: '24px', fontSize: '14px' }}>
        These are the global defaults. Per-engagement overrides can be set when creating an engagement.
      </p>

      <div className="settings-sections">
        {SECTIONS.map(section => (
          <div key={section.key} className="settings-section">
            <div className="section-header">
              <h2>{section.label}</h2>
              {section.description && (
                <p className="section-desc">{section.description}</p>
              )}
            </div>
            <div className="settings-fields">
              {section.fields.map(field => (
                <div key={field.key} className="settings-field">
                  <div className="field-label-row">
                    <label>{field.label}</label>
                    {field.help && <span className="field-help">{field.help}</span>}
                  </div>
                  <div className="field-input">
                    {renderField(section.key, field)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}


function TagsInput({ value, onChange }) {
  const [input, setInput] = useState('')

  function addTag(e) {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      const tag = input.trim().replace(/,$/, '')
      if (tag && !value.includes(tag)) {
        onChange([...value, tag])
      }
      setInput('')
    }
  }

  function removeTag(tag) {
    onChange(value.filter(t => t !== tag))
  }

  return (
    <div className="tags-input">
      <div className="tags-list">
        {value.map(tag => (
          <span key={tag} className="tag">
            {tag}
            <button className="tag-remove" onClick={() => removeTag(tag)}>&times;</button>
          </span>
        ))}
      </div>
      <input
        type="text"
        value={input}
        onChange={e => setInput(e.target.value)}
        onKeyDown={addTag}
        placeholder="Type and press Enter"
        className="tag-input"
      />
    </div>
  )
}
