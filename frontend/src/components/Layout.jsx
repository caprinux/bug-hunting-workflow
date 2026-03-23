import React from 'react'
import { Link, useLocation } from 'react-router-dom'

export default function Layout({ children, theme, toggleTheme }) {
  const location = useLocation()

  return (
    <div className="layout">
      <header className="header">
        <div className="header-left">
          <Link to="/" className="logo">Bug Hunting Workflow</Link>
        </div>
        <nav className="header-nav">
          <Link to="/" className={location.pathname === '/' ? 'active' : ''}>Dashboard</Link>
          <Link to="/engagements/new" className={location.pathname === '/engagements/new' ? 'active' : ''}>
            New Engagement
          </Link>
        </nav>
        <div className="header-right">
          <button className="theme-toggle" onClick={toggleTheme} title="Toggle theme">
            {theme === 'dark' ? 'Light' : 'Dark'}
          </button>
        </div>
      </header>
      <main className="main-content">
        {children}
      </main>
    </div>
  )
}
