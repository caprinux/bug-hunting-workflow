import React, { useState } from 'react'
import { api } from '../utils/api'
import { setAuthToken } from '../hooks/useWebSocket'

export default function Login({ onLogin, theme, toggleTheme }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const token = await api.login(password)
      sessionStorage.setItem('bhw_token', token)
      setAuthToken(token)
      onLogin()
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  return (
    <div className="login-page" data-theme={theme}>
      <div className="login-card">
        <div className="login-brand">
          <svg className="login-icon" width="40" height="40" viewBox="0 0 40 40" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="20" cy="20" r="14" opacity="0.3" />
            <circle cx="20" cy="20" r="8" opacity="0.6" />
            <circle cx="20" cy="20" r="2" fill="currentColor" stroke="none" />
            <path d="M20 2v8M20 30v8M2 20h8M30 20h8" />
          </svg>
          <h1 className="login-title">Bug Hunting Workflow</h1>
          <p className="login-subtitle">Automated Vulnerability Discovery</p>
        </div>
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              placeholder="Enter authentication password"
              autoFocus
              required
            />
          </div>
          {error && <div className="error-msg">{error}</div>}
          <button type="submit" className="btn btn-primary btn-large" disabled={loading}
                  style={{ width: '100%' }}>
            {loading ? 'Authenticating...' : 'Authenticate'}
          </button>
        </form>
        <button className="btn-icon login-theme-toggle" onClick={toggleTheme}
                title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
          {theme === 'dark' ? (
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <circle cx="8" cy="8" r="3" />
              <path d="M8 1.5v1.5M8 13v1.5M2.75 2.75l1.06 1.06M12.19 12.19l1.06 1.06M1.5 8H3M13 8h1.5M2.75 13.25l1.06-1.06M12.19 3.81l1.06-1.06" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M13.5 8.5A5.5 5.5 0 117.5 2.5a4.5 4.5 0 006 6z" />
            </svg>
          )}
        </button>
      </div>
    </div>
  )
}
