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
      await api.login(password)
      sessionStorage.setItem('bhw_token', password)
      setAuthToken(password)
      onLogin()
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  return (
    <div className="login-page" data-theme={theme}>
      <div className="login-card">
        <h1>Bug Hunting Workflow</h1>
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
            {loading ? 'Authenticating...' : 'Log In'}
          </button>
        </form>
        <button className="theme-toggle" onClick={toggleTheme}
                style={{ marginTop: '16px' }}>
          {theme === 'dark' ? 'Light' : 'Dark'} Theme
        </button>
      </div>
    </div>
  )
}
