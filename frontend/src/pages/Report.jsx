import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'

/**
 * Simple markdown-to-HTML renderer.
 */
function renderMarkdown(md) {
  if (!md) return ''

  const lines = md.split('\n')
  const html = []
  let inCodeBlock = false
  let codeBuffer = []
  let inList = false

  for (const line of lines) {
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        html.push(`<pre class="md-code-block"><code>${escapeHtml(codeBuffer.join('\n'))}</code></pre>`)
        codeBuffer = []
        inCodeBlock = false
      } else {
        if (inList) { html.push('</ul>'); inList = false }
        inCodeBlock = true
      }
      continue
    }
    if (inCodeBlock) { codeBuffer.push(line); continue }

    const trimmed = line.trim()
    if (!trimmed) { if (inList) { html.push('</ul>'); inList = false }; continue }
    if (/^---+$/.test(trimmed) || /^===+$/.test(trimmed)) {
      if (inList) { html.push('</ul>'); inList = false }
      html.push('<hr class="md-hr"/>'); continue
    }

    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)/)
    if (headerMatch) {
      if (inList) { html.push('</ul>'); inList = false }
      const level = headerMatch[1].length
      html.push(`<h${level} class="md-h${level}">${inlineFormat(headerMatch[2])}</h${level}>`); continue
    }

    if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || /^\d+\.\s/.test(trimmed)) {
      if (!inList) { html.push('<ul class="md-list">'); inList = true }
      const content = trimmed.replace(/^[-*]\s+/, '').replace(/^\d+\.\s+/, '')
      html.push(`<li>${inlineFormat(content)}</li>`); continue
    }

    if (inList) { html.push('</ul>'); inList = false }
    html.push(`<p class="md-p">${inlineFormat(trimmed)}</p>`)
  }

  if (inList) html.push('</ul>')
  if (inCodeBlock && codeBuffer.length)
    html.push(`<pre class="md-code-block"><code>${escapeHtml(codeBuffer.join('\n'))}</code></pre>`)
  return html.join('\n')
}

function inlineFormat(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code class="md-inline-code">$1</code>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

function escapeHtml(text) {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}


export default function Report() {
  const { id } = useParams()
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [genMessage, setGenMessage] = useState('')
  const [error, setError] = useState(null)

  function loadReport() {
    setLoading(true)
    api.getReport(id)
      .then(data => { setReport(data.content); setError(null) })
      .catch(e => { setReport(null); setError(e.message) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadReport() }, [id])

  async function handleGenerate() {
    setGenerating(true)
    setGenMessage('Generating report...')
    setError(null)
    try {
      await api.generateReport(id)
      // Poll for completion
      for (let i = 0; i < 120; i++) {
        await new Promise(r => setTimeout(r, 2000))
        try {
          const status = await api.reportStatus(id)
          setGenMessage(status.message || status.status)
          if (status.status === 'completed') {
            loadReport()
            setGenerating(false)
            setGenMessage('')
            return
          } else if (status.status === 'failed') {
            setError(status.message)
            setGenerating(false)
            setGenMessage('')
            return
          }
        } catch { break }
      }
      setGenerating(false)
      setGenMessage('')
    } catch (e) {
      setError(e.message)
      setGenerating(false)
      setGenMessage('')
    }
  }

  if (loading) return <div className="loading">Loading report...</div>

  return (
    <div className="page report-page">
      <div className="page-header">
        <div>
          <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
          <h1>Summary Report</h1>
        </div>
        <div className="header-actions">
          {generating ? (
            <span style={{ fontSize: '13px', color: 'var(--color-info)' }}>{genMessage}</span>
          ) : (
            <button className="btn btn-primary" onClick={handleGenerate}>
              {report ? 'Regenerate Report' : 'Generate Report'}
            </button>
          )}
          {report && (
            <button className="btn btn-secondary" onClick={() => {
              const blob = new Blob([report], { type: 'text/markdown' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url; a.download = 'engagement_report.md'; a.click()
              URL.revokeObjectURL(url)
            }}>Download .md</button>
          )}
        </div>
      </div>

      {error && !report && (
        <div className="empty-state">
          <p>No report generated yet. Click "Generate Report" to create one.</p>
        </div>
      )}

      {report && (
        <div className="report-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(report) }} />
      )}
    </div>
  )
}
