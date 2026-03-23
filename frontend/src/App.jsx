import React, { useState, useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import NewEngagement from './pages/NewEngagement'
import EngagementDetail from './pages/EngagementDetail'
import RunDetail from './pages/RunDetail'
import BugBrowser from './pages/BugBrowser'
import ChainBrowser from './pages/ChainBrowser'
import IntelBrowser from './pages/IntelBrowser'

export default function App() {
  const [theme, setTheme] = useState(() => localStorage.getItem('theme') || 'dark')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')

  return (
    <Layout theme={theme} toggleTheme={toggleTheme}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/engagements/new" element={<NewEngagement />} />
        <Route path="/engagements/:id" element={<EngagementDetail />} />
        <Route path="/engagements/:id/runs/:runId" element={<RunDetail />} />
        <Route path="/engagements/:id/bugs" element={<BugBrowser />} />
        <Route path="/engagements/:id/chains" element={<ChainBrowser />} />
        <Route path="/engagements/:id/intel" element={<IntelBrowser />} />
        <Route path="*" element={<Navigate to="/" />} />
      </Routes>
    </Layout>
  )
}
