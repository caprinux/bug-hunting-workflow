import React, { useState, useEffect } from 'react'
import { api } from '../utils/api'

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

function UpIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 12V4M4.5 7.5L8 4l3.5 3.5" />
    </svg>
  )
}

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
          <span className="file-icon"><UpIcon /></span>
          <span className="file-name">..</span>
        </div>
      )}

      {files.map(file => (
        <div
          key={file.path}
          className="file-item"
          onClick={() => loadDirectory(file.path)}
        >
          <span className="file-icon">
            {file.is_dir ? <FolderIcon /> : <FileIcon />}
          </span>
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
