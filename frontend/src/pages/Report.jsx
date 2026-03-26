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
  let codeLang = ''
  let codeBuffer = []
  let inList = false
  let inTable = false
  let tableRows = []

  function flushList() { if (inList) { html.push('</ul>'); inList = false } }
  function flushTable() {
    if (!inTable) return
    inTable = false
    if (tableRows.length === 0) return
    const headerCells = tableRows[0]
    let bodyRows = tableRows.slice(1)
    // Remove separator row (|---|---|)
    if (bodyRows.length > 0 && bodyRows[0].every(c => /^[-:]+$/.test(c.trim()))) {
      bodyRows = bodyRows.slice(1)
    }
    let t = '<table class="md-table"><thead><tr>'
    headerCells.forEach(c => { t += `<th>${inlineFormat(c.trim())}</th>` })
    t += '</tr></thead><tbody>'
    bodyRows.forEach(row => {
      t += '<tr>'
      row.forEach(c => { t += `<td>${inlineFormat(c.trim())}</td>` })
      t += '</tr>'
    })
    t += '</tbody></table>'
    html.push(t)
    tableRows = []
  }

  for (const line of lines) {
    // Code blocks
    if (line.startsWith('```')) {
      if (inCodeBlock) {
        const langClass = codeLang ? ` language-${codeLang}` : ''
        html.push(`<pre class="md-code-block${langClass}" data-lang="${codeLang}"><code>${highlightCode(codeBuffer.join('\n'), codeLang)}</code></pre>`)
        codeBuffer = []
        inCodeBlock = false
        codeLang = ''
      } else {
        flushList(); flushTable()
        codeLang = line.slice(3).trim().toLowerCase()
        inCodeBlock = true
      }
      continue
    }
    if (inCodeBlock) { codeBuffer.push(line); continue }

    const trimmed = line.trim()

    // Table rows
    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      flushList()
      inTable = true
      const cells = trimmed.slice(1, -1).split('|')
      tableRows.push(cells)
      continue
    } else if (inTable) {
      flushTable()
    }

    if (!trimmed) { flushList(); continue }

    // Horizontal rule
    if (/^---+$/.test(trimmed) || /^===+$/.test(trimmed)) {
      flushList()
      html.push('<hr class="md-hr"/>'); continue
    }

    // Headers
    const headerMatch = trimmed.match(/^(#{1,6})\s+(.+)/)
    if (headerMatch) {
      flushList()
      const level = headerMatch[1].length
      html.push(`<h${level} class="md-h${level}">${inlineFormat(headerMatch[2])}</h${level}>`); continue
    }

    // List items
    if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || /^\d+\.\s/.test(trimmed)) {
      if (!inList) { html.push('<ul class="md-list">'); inList = true }
      const content = trimmed.replace(/^[-*]\s+/, '').replace(/^\d+\.\s+/, '')
      html.push(`<li>${inlineFormat(content)}</li>`); continue
    }

    flushList()
    html.push(`<p class="md-p">${inlineFormat(trimmed)}</p>`)
  }

  flushList()
  flushTable()
  if (inCodeBlock && codeBuffer.length) {
    const langClass = codeLang ? ` language-${codeLang}` : ''
    html.push(`<pre class="md-code-block${langClass}" data-lang="${codeLang}"><code>${highlightCode(codeBuffer.join('\n'), codeLang)}</code></pre>`)
  }
  return html.join('\n')
}

/**
 * Basic syntax highlighting for common languages.
 */
function highlightCode(code, lang) {
  const escaped = escapeHtml(code)
  if (!lang) return escaped

  let highlighted = escaped

  if (['python', 'py'].includes(lang)) {
    highlighted = highlighted
      .replace(/\b(import|from|def|class|return|if|elif|else|for|while|in|not|and|or|is|None|True|False|try|except|raise|with|as|pass|break|continue|yield|async|await|lambda|print)\b/g, '<span class="hl-kw">$1</span>')
      .replace(/(#.*)/g, '<span class="hl-comment">$1</span>')
      .replace(/(&quot;.*?&quot;|&#x27;.*?&#x27;|&quot;&quot;&quot;[\s\S]*?&quot;&quot;&quot;)/g, '<span class="hl-str">$1</span>')
      .replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-num">$1</span>')
  } else if (['javascript', 'js', 'typescript', 'ts'].includes(lang)) {
    highlighted = highlighted
      .replace(/\b(const|let|var|function|return|if|else|for|while|of|in|new|this|class|import|export|from|async|await|try|catch|throw|null|undefined|true|false)\b/g, '<span class="hl-kw">$1</span>')
      .replace(/(\/\/.*)/g, '<span class="hl-comment">$1</span>')
      .replace(/(&quot;.*?&quot;|&#x27;.*?&#x27;|`.*?`)/g, '<span class="hl-str">$1</span>')
      .replace(/\b(\d+\.?\d*)\b/g, '<span class="hl-num">$1</span>')
  } else if (['bash', 'sh', 'shell', 'zsh'].includes(lang)) {
    highlighted = highlighted
      .replace(/(#.*)/g, '<span class="hl-comment">$1</span>')
      .replace(/(&quot;.*?&quot;|&#x27;.*?&#x27;)/g, '<span class="hl-str">$1</span>')
      .replace(/\$\w+/g, '<span class="hl-var">$&</span>')
      .replace(/\b(curl|wget|grep|sed|awk|cat|echo|export|sudo|pip|npm|git|docker|python3?|node)\b/g, '<span class="hl-kw">$1</span>')
  } else if (['json'].includes(lang)) {
    highlighted = highlighted
      .replace(/(&quot;[^&]*?&quot;)\s*:/g, '<span class="hl-key">$1</span>:')
      .replace(/:\s*(&quot;.*?&quot;)/g, ': <span class="hl-str">$1</span>')
      .replace(/:\s*\b(true|false|null)\b/g, ': <span class="hl-kw">$1</span>')
      .replace(/:\s*\b(\d+\.?\d*)\b/g, ': <span class="hl-num">$1</span>')
  } else if (['sql'].includes(lang)) {
    highlighted = highlighted
      .replace(/\b(SELECT|FROM|WHERE|INSERT|INTO|UPDATE|SET|DELETE|CREATE|DROP|ALTER|TABLE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|AND|OR|NOT|IN|VALUES|ORDER|BY|GROUP|HAVING|LIMIT|UNION|AS|NULL|IS|LIKE|BETWEEN|EXISTS|COUNT|SUM|AVG|MAX|MIN|DISTINCT)\b/gi, '<span class="hl-kw">$1</span>')
      .replace(/(--.*)/g, '<span class="hl-comment">$1</span>')
      .replace(/(&#x27;.*?&#x27;)/g, '<span class="hl-str">$1</span>')
  } else if (['elixir', 'ex'].includes(lang)) {
    highlighted = highlighted
      .replace(/\b(def|defp|defmodule|do|end|if|else|case|cond|when|fn|use|alias|import|require|with|raise|try|catch|rescue|after)\b/g, '<span class="hl-kw">$1</span>')
      .replace(/(#.*)/g, '<span class="hl-comment">$1</span>')
      .replace(/(&quot;.*?&quot;)/g, '<span class="hl-str">$1</span>')
      .replace(/(:\w+)/g, '<span class="hl-sym">$1</span>')
  }

  return highlighted
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
