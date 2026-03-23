import React, { useState, useEffect } from 'react'
import { api } from '../utils/api'

export default function StageOutputBrowser({ engagementId, runId, stageName, onClose }) {
  const [files, setFiles] = useState([])
  const [content, setContent] = useState(null)
  const [currentPath, setCurrentPath] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

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
          <span className="file-icon">^</span>
          <span className="file-name">..</span>
        </div>
      )}

      {files.map(file => (
        <div
          key={file.path}
          className="file-item"
          onClick={() => loadDirectory(file.path)}
        >
          <span className="file-icon">{file.is_dir ? 'D' : 'F'}</span>
          <span className="file-name">{file.name}</span>
          {file.size !== undefined && (
            <span className="file-size">{formatSize(file.size)}</span>
          )}
        </div>
      ))}

      {content && (
        <div className="file-content">
          <pre>{typeof content.content === 'string'
            ? content.content
            : JSON.stringify(content.content, null, 2)
          }</pre>
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
