import React, { useState, useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import 'highlight.js/styles/github-dark.css'
import { api } from '../utils/api'


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

  // Check if a report is already being generated (e.g. auto-triggered after run)
  useEffect(() => {
    let cancelled = false
    api.reportStatus(id).then(status => {
      if (cancelled) return
      if (status.status === 'running') {
        setGenerating(true)
        setGenMessage(status.message || 'Report is being generated...')
        pollStatus()
      }
    }).catch(() => {})
    return () => { cancelled = true }
  }, [id])

  function pollStatus() {
    const interval = setInterval(async () => {
      try {
        const status = await api.reportStatus(id)
        setGenMessage(status.message || status.status)
        if (status.status === 'completed') {
          clearInterval(interval)
          setGenerating(false)
          setGenMessage('')
          loadReport()
        } else if (status.status === 'failed') {
          clearInterval(interval)
          setGenerating(false)
          setGenMessage('')
          setError(status.message)
        }
      } catch {
        clearInterval(interval)
        setGenerating(false)
      }
    }, 2000)
    return interval
  }

  async function handleGenerate() {
    setGenerating(true)
    setGenMessage('Generating report...')
    setError(null)
    try {
      await api.generateReport(id)
      pollStatus()
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
        <div className="report-content">
          <Markdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
            {report}
          </Markdown>
        </div>
      )}
    </div>
  )
}
