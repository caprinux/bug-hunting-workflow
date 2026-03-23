import React from 'react'
import { Link, useLocation } from 'react-router-dom'

export default function Layout({ children, theme, toggleTheme, onLogout }) {
  const location = useLocation()
  const isActive = (path) => location.pathname === path ? 'active' : ''

  return (
    <div className="layout">
      <header className="header">
        <Link to="/" className="logo">
          <svg className="logo-icon" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
            <circle cx="10" cy="10" r="7" />
            <circle cx="10" cy="10" r="2.5" />
            <path d="M10 1v4M10 15v4M1 10h4M15 10h4" />
          </svg>
          <span className="logo-text">BHW</span>
        </Link>
        <nav className="header-nav">
          <Link to="/" className={isActive('/')}>Dashboard</Link>
          <Link to="/engagements/new" className={isActive('/engagements/new')}>
            New Engagement
          </Link>
          <Link to="/settings" className={isActive('/settings')}>Settings</Link>
        </nav>
        <div className="header-actions">
          <button className="btn-icon" onClick={toggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
            {theme === 'dark' ? (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
                <circle cx="8" cy="8" r="3" />
                <path d="M8 1.5v1.5M8 13v1.5M2.75 2.75l1.06 1.06M12.19 12.19l1.06 1.06M1.5 8H3M13 8h1.5M2.75 13.25l1.06-1.06M12.19 3.81l1.06-1.06" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M13.5 8.5A5.5 5.5 0 117.5 2.5a4.5 4.5 0 006 6z" />
              </svg>
            )}
          </button>
          {onLogout && (
            <button className="btn-icon" onClick={onLogout} title="Log out">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 14H3a1 1 0 01-1-1V3a1 1 0 011-1h3M11 11l3-3-3-3M5.5 8H14" />
              </svg>
            </button>
          )}
        </div>
      </header>
      <main className="main-content">
        {children}
      </main>
    </div>
  )
}
