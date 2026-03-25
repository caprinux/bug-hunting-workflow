import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../utils/api'

/**
 * Simple markdown-to-HTML renderer.
 * Handles headers, bold, code blocks, inline code, lists, links, and horizontal rules.
 */
function renderMarkdown(md) {
  if (!md) return ''

  const lines = md.split('\n')
  const html = []
  let inCodeBlock = false
  let codeBuffer = []
  let inList = false

  for (const line of lines) {
    // Code blocks
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
    if (inCodeBlock) {
      codeBuffer.push(line)
      continue
    }

    const trimmed = line.trim()

    // Empty line
    if (!trimmed) {
      if (inList) { html.push('</ul>'); inList = false }
      continue
    }

    // Horizontal rule
    if (/^---+$/.test(trimmed) || /^===+$/.test(trimmed)) {
      if (inList) { html.push('</ul>'); inList = false }
      html.push('<hr class="md-hr"/>')
      continue
    }

    // Headers
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)/)
    if (headerMatch) {
      if (inList) { html.push('</ul>'); inList = false }
      const level = headerMatch[1].length
      const text = inlineFormat(headerMatch[2])
      html.push(`<h${level} class="md-h${level}">${text}</h${level}>`)
      continue
    }

    // List items
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || /^\d+\.\s/.test(trimmed)) {
      if (!inList) { html.push('<ul class="md-list">'); inList = true }
      const content = trimmed.replace(/^[-*]\s+/, '').replace(/^\d+\.\s+/, '')
      html.push(`<li>${inlineFormat(content)}</li>`)
      continue
    }

    // Regular paragraph
    if (inList) { html.push('</ul>'); inList = false }
    html.push(`<p class="md-p">${inlineFormat(trimmed)}</p>`)
  }

  if (inList) html.push('</ul>')
  if (inCodeBlock && codeBuffer.length) {
    html.push(`<pre class="md-code-block"><code>${escapeHtml(codeBuffer.join('\n'))}</code></pre>`)
  }

  return html.join('\n')
}

function inlineFormat(text) {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.+?)`/g, '<code class="md-inline-code">$1</code>')
    .replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
}

function escapeHtml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}


export default function Report() {
  const { id } = useParams()
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.getReport(id)
      .then(data => setReport(data.content))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <div className="loading">Loading report...</div>
  if (error) return (
    <div className="page">
      <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
      <div className="empty-state"><p>No report generated yet. Run the full pipeline to generate a summary report.</p></div>
    </div>
  )

  return (
    <div className="page report-page">
      <div className="page-header">
        <div>
          <Link to={`/engagements/${id}`} className="back-link">Back to Engagement</Link>
          <h1>Summary Report</h1>
        </div>
        <div className="header-actions">
          <button className="btn btn-secondary" onClick={() => {
            const blob = new Blob([report], { type: 'text/markdown' })
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url; a.download = 'engagement_report.md'; a.click()
            URL.revokeObjectURL(url)
          }}>Download .md</button>
        </div>
      </div>
      <div className="report-content" dangerouslySetInnerHTML={{ __html: renderMarkdown(report) }} />
    </div>
  )
}
