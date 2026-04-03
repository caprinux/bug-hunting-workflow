import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../utils/api'
import useTitle from '../hooks/useTitle'

export default function Platforms() {
  useTitle('Platforms')
  const navigate = useNavigate()
  const [platforms, setPlatforms] = useState([])
  const [programs, setPrograms] = useState([])
  const [selectedPlatform, setSelectedPlatform] = useState(null)
  const [selectedProgram, setSelectedProgram] = useState(null)
  const [programDetails, setProgramDetails] = useState(null)
  const [loading, setLoading] = useState(true)
  const [scraping, setScraping] = useState(false)
  const [importing, setImporting] = useState(false)
  const [showScrapeModal, setShowScrapeModal] = useState(false)
  const [scrapeForm, setScrapeForm] = useState({})
  const [search, setSearch] = useState('')
  const [error, setError] = useState(null)

  useEffect(() => {
    api.listPlatforms().then(async (data) => {
      setPlatforms(data)
      if (data.length > 0) {
        setSelectedPlatform(data[0])
        if (data[0].programs_count > 0) {
          loadPrograms(data[0].name)
        }
        // Check if a scrape is already running (e.g., page was reloaded)
        try {
          const status = await api.scrapeStatus(data[0].name)
          if (status.status === 'running') {
            setScraping(true)
            setScrapeMessage(status.message || 'Scraping...')
            pollScrapeStatus(data[0].name)
          }
        } catch {}
      }
    }).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  async function loadPrograms(platformName) {
    try {
      const data = await api.listPlatformPrograms(platformName)
      setPrograms(data)
    } catch (e) {
      setError(e.message)
    }
  }

  const [scrapeMessage, setScrapeMessage] = useState('')

  async function pollScrapeStatus(platformName) {
    for (let i = 0; i < 600; i++) {  // max 20 min (117 programs × 0.5s + overhead)
      await new Promise(r => setTimeout(r, 2000))
      try {
        const status = await api.scrapeStatus(platformName)
        setScrapeMessage(status.message || status.status)
        if (status.status === 'completed') {
          await loadPrograms(platformName)
          const updated = await api.listPlatforms()
          setPlatforms(updated)
          setSelectedPlatform(updated.find(p => p.name === platformName) || selectedPlatform)
          setScraping(false)
          setScrapeMessage('')
          return
        } else if (status.status === 'failed') {
          setError(status.message)
          setScraping(false)
          setScrapeMessage('')
          return
        }
      } catch { break }
    }
    setScraping(false)
    setScrapeMessage('')
  }

  async function handleScrape() {
    if (!selectedPlatform) return
    setScraping(true)
    setError(null)
    setScrapeMessage('Starting...')
    try {
      await api.scrapePlatform(selectedPlatform.name, scrapeForm)
      setShowScrapeModal(false)
      setScrapeForm({})
      pollScrapeStatus(selectedPlatform.name)
    } catch (e) {
      setError(e.message)
      setScraping(false)
      setScrapeMessage('')
    }
  }

  async function handleSelectProgram(program) {
    setSelectedProgram(program)
    setProgramDetails(null)
    try {
      const details = await api.getPlatformProgram(selectedPlatform.name, program.id)
      setProgramDetails(details)
    } catch (e) {
      setError(e.message)
    }
  }

  async function handleImport() {
    if (!selectedPlatform || !selectedProgram) return
    setImporting(true)
    setError(null)
    try {
      await api.importProgram(selectedPlatform.name, selectedProgram.id)

      // Import is synchronous — fetch result immediately
      const status = await api.importStatus(selectedPlatform.name, selectedProgram.id)
      if (status.status === 'completed') {
        navigate('/engagements/new', { state: { prefill: status.result } })
        return
      } else if (status.status === 'failed') {
        setError(status.message || 'Import failed')
        setImporting(false)
        return
      }
      setError('Import failed — unexpected status')
    } catch (e) {
      setError(e.message)
    }
    setImporting(false)
  }

  const filteredPrograms = programs.filter(p =>
    !search || p.name.toLowerCase().includes(search.toLowerCase()) ||
    p.id.toLowerCase().includes(search.toLowerCase())
  )

  if (loading) return <div className="loading">Loading platforms...</div>

  return (
    <div className="page platforms-page">
      <div className="page-header">
        <h1>Bug Bounty Programs</h1>
        <div className="header-actions">
          {selectedPlatform && (
            <>
              {selectedPlatform.last_scraped && (
                <span className="muted" style={{ fontSize: '12px' }}>
                  Last scraped: {new Date(selectedPlatform.last_scraped).toLocaleString()}
                </span>
              )}
              {scraping ? (
                <span style={{ fontSize: '13px', color: 'var(--color-info)' }}>
                  {scrapeMessage || 'Scraping...'}
                </span>
              ) : (
                <button className="btn btn-primary" onClick={() => setShowScrapeModal(true)}>
                  Scrape {selectedPlatform.display_name}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      {error && <div className="error-msg">{error}</div>}

      {/* Platform tabs */}
      {platforms.length > 1 && (
        <div className="filters" style={{ marginBottom: '12px' }}>
          {platforms.map(p => (
            <button key={p.name}
              className={`btn btn-sm ${selectedPlatform?.name === p.name ? 'active' : ''}`}
              onClick={() => { setSelectedPlatform(p); loadPrograms(p.name); setSelectedProgram(null) }}>
              {p.display_name} ({p.programs_count})
            </button>
          ))}
        </div>
      )}

      {/* Scrape modal */}
      {showScrapeModal && selectedPlatform && (
        <div className="scrape-modal">
          <h3>Authenticate to {selectedPlatform.display_name}</h3>
          {selectedPlatform.credential_fields.map(field => (
            <div key={field.name} className="form-group">
              <label>{field.label} {!field.required && <span className="muted">(optional)</span>}</label>
              <input
                type={field.type}
                value={scrapeForm[field.name] || ''}
                onChange={e => setScrapeForm(f => ({ ...f, [field.name]: e.target.value }))}
                placeholder={field.label}
              />
            </div>
          ))}
          <div style={{ display: 'flex', gap: '8px', marginTop: '12px' }}>
            <button className="btn btn-primary" onClick={handleScrape} disabled={scraping}>
              {scraping ? 'Scraping...' : 'Start Scraping'}
            </button>
            <button className="btn btn-secondary" onClick={() => setShowScrapeModal(false)}>Cancel</button>
          </div>
        </div>
      )}

      {/* Search */}
      {programs.length > 0 && (
        <div className="form-group" style={{ marginBottom: '16px' }}>
          <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                 placeholder="Search programs..." style={{ maxWidth: '400px' }} />
        </div>
      )}

      <div className="platforms-layout">
        {/* Program list */}
        <div className="program-list">
          {filteredPrograms.length === 0 && programs.length === 0 && (
            <div className="empty-state">
              <p>No programs cached. Click "Scrape" to fetch from {selectedPlatform?.display_name || 'the platform'}.</p>
            </div>
          )}
          {filteredPrograms.map(p => (
            <div key={p.id}
              className={`program-card ${selectedProgram?.id === p.id ? 'selected' : ''}`}
              onClick={() => handleSelectProgram(p)}>
              <div className="program-card-header">
                <span className="program-name">{p.name}</span>
                {p.bounty && <span className="badge success">Bounty</span>}
              </div>
              <div className="program-card-meta">
                <span>{p.scope_count} targets</span>
                {p.reward_max > 0 && <span className="cost">Up to €{p.reward_max.toLocaleString()}</span>}
              </div>
            </div>
          ))}
        </div>

        {/* Program details */}
        <div className="program-detail">
          {!selectedProgram && (
            <div className="empty-state"><p>Select a program to view details</p></div>
          )}
          {selectedProgram && !programDetails && (
            <div className="loading">Loading details...</div>
          )}
          {programDetails && (
            <>
              <div className="program-detail-header">
                <h2>{programDetails.name}</h2>
                <button className="btn btn-primary" onClick={handleImport} disabled={importing}>
                  {importing ? 'Importing...' : 'Import as Engagement'}
                </button>
              </div>

              <div className="program-detail-section">
                <h3>In-Scope Assets ({programDetails.scopes?.length || 0})</h3>
                <div className="program-scopes">
                  {(programDetails.scopes || []).map((s, i) => (
                    <div key={i} className="scope-item">
                      <span className="scope-target">{s.scope}</span>
                      <span className="scope-type">{s.scope_type_name}</span>
                      <span className={`severity-badge ${(s.asset_value || '').toLowerCase()}`}>{s.asset_value}</span>
                    </div>
                  ))}
                </div>
              </div>

              {programDetails.out_of_scope?.length > 0 && (
                <div className="program-detail-section">
                  <h3>Out of Scope ({programDetails.out_of_scope.length})</h3>
                  <ul className="scope-list-plain">
                    {programDetails.out_of_scope.map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}

              <div className="program-detail-section">
                <h3>Qualifying Vulnerabilities ({programDetails.qualifying_vulns?.length || 0})</h3>
                <div className="vuln-tags">
                  {(programDetails.qualifying_vulns || []).map((v, i) => (
                    <span key={i} className="vuln-tag qualifying">{v}</span>
                  ))}
                </div>
              </div>

              {programDetails.non_qualifying_vulns?.length > 0 && (
                <div className="program-detail-section">
                  <h3>Non-Qualifying ({programDetails.non_qualifying_vulns.length})</h3>
                  <div className="vuln-tags">
                    {programDetails.non_qualifying_vulns.map((v, i) => (
                      <span key={i} className="vuln-tag non-qualifying">{v}</span>
                    ))}
                  </div>
                </div>
              )}

              {programDetails.rules_text && (
                <div className="program-detail-section">
                  <h3>Program Rules</h3>
                  <p style={{ fontSize: '13px', whiteSpace: 'pre-wrap' }}>
                    {programDetails.rules_text.slice(0, 2000)}
                    {programDetails.rules_text.length > 2000 && '...'}
                  </p>
                </div>
              )}

              <div className="program-detail-section">
                <h3>Info</h3>
                <div style={{ fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  {programDetails.vpn_required && <span>VPN Required</span>}
                  {programDetails.account_access && <span>Account Access: {programDetails.account_access}</span>}
                  {programDetails.reward_max > 0 && <span>Rewards: €{programDetails.reward_min} — €{programDetails.reward_max}</span>}
                </div>
              </div>

              {programDetails.hunter_credentials?.length > 0 && (
                <div className="program-detail-section">
                  <h3>Credentials ({programDetails.hunter_credentials.length})</h3>
                  <div className="program-scopes">
                    {programDetails.hunter_credentials.map((cred, i) => (
                      <div key={i} className="scope-item" style={{ flexDirection: 'column', alignItems: 'flex-start', gap: '4px' }}>
                        {cred.access_type && <strong>{cred.access_type}</strong>}
                        {cred.login && <span className="scope-target">{cred.login}</span>}
                        {cred.password && <span className="muted">{cred.password}</span>}
                        {cred.url && <span className="scope-target">{cred.url}</span>}
                        {cred.description && <span className="muted">{cred.description}</span>}
                        {/* Fallback: show raw fields if structure is different */}
                        {!cred.login && !cred.access_type && (
                          <pre style={{ margin: 0, fontSize: '12px' }}>{JSON.stringify(cred, null, 2)}</pre>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
