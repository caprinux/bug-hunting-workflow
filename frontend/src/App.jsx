import React, { useState, useEffect, useCallback } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import NewEngagement from './pages/NewEngagement'
import EngagementDetail from './pages/EngagementDetail'
import RunDetail from './pages/RunDetail'
import BugBrowser from './pages/BugBrowser'
import ChainBrowser from './pages/ChainBrowser'
import IntelBrowser from './pages/IntelBrowser'
import Report from './pages/Report'
import Chat from './pages/Chat'
import Settings from './pages/Settings'
import Usage from './pages/Usage'
import Platforms from './pages/Platforms'

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')
  const [authenticated, setAuthenticated] = useState(() => !!localStorage.getItem('bhw_token'))

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  // Poll for token removal (from WebSocket auth failure or API 401)
  useEffect(() => {
    if (!authenticated) return
    const interval = setInterval(() => {
      if (!localStorage.getItem('bhw_token')) {
        setAuthenticated(false)
      }
    }, 1000)
    return () => clearInterval(interval)
  }, [authenticated])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  const handleLogout = useCallback(() => {
    localStorage.removeItem('bhw_token')
    setAuthenticated(false)
  }, [])

  if (!authenticated) {
    return <Login onLogin={() => setAuthenticated(true)} theme={theme} toggleTheme={toggleTheme} />
  }

  return (
    <Layout theme={theme} toggleTheme={toggleTheme} onLogout={handleLogout}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/engagements/new" element={<NewEngagement />} />
        <Route path="/engagements/:id" element={<EngagementDetail />} />
        <Route path="/engagements/:id/runs/:runId" element={<RunDetail />} />
        <Route path="/engagements/:id/bugs" element={<BugBrowser />} />
        <Route path="/engagements/:id/chains" element={<ChainBrowser />} />
        <Route path="/engagements/:id/intel" element={<IntelBrowser />} />
        <Route path="/engagements/:id/report" element={<Report />} />
        <Route path="/engagements/:id/chat" element={<Chat />} />
        <Route path="/platforms" element={<Platforms />} />
        <Route path="/usage" element={<Usage />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  )
}
