import { useState, useEffect, useRef, useCallback } from 'react'

function getAuthToken() {
  // The frontend stores the signed session token after a successful login.
  return localStorage.getItem('bhw_token') || ''
}

export function setAuthToken(token) {
  localStorage.setItem('bhw_token', token)
}

export function useWebSocket(engagementId = null) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getAuthToken()
    const params = new URLSearchParams()
    if (token) params.set('token', token)
    if (engagementId) params.set('engagement_id', engagementId)
    const qs = params.toString()
    const url = `${protocol}//${window.location.host}/ws${qs ? '?' + qs : ''}`

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
    }

    ws.onmessage = (evt) => {
      try {
        const data = JSON.parse(evt.data)
        setEvents(prev => [...prev.slice(-500), data])

        if ('Notification' in window && Notification.permission === 'granted') {
          if (data.type === 'completion') {
            new Notification('Bug Hunting Workflow', {
              body: `Run completed: ${data.data?.run_confirmed_bugs || 0} bugs confirmed (${data.data?.cumulative_confirmed_bugs || 0} total)`,
            })
          } else if (data.type === 'stage_update' && data.data?.status === 'failed') {
            new Notification('Bug Hunting Workflow — Stage Failed', {
              body: `Stage "${data.stage}" failed in run`,
              tag: `stage-fail-${data.stage}`,
            })
          } else if (data.type === 'error' && data.stage) {
            new Notification('Bug Hunting Workflow — Error', {
              body: `${data.stage}: ${data.data?.error?.slice(0, 100) || 'Unknown error'}`,
              tag: `error-${data.stage}`,
            })
          }
        }
      } catch (e) {
        console.error('WebSocket parse error:', e)
      }
    }

    ws.onclose = (evt) => {
      setConnected(false)
      // 1008 = Policy Violation (server rejected auth). Don't reconnect.
      if (evt.code === 1008) {
        console.warn('WebSocket auth rejected — clearing token')
        localStorage.removeItem('bhw_token')
        return
      }
      reconnectTimer.current = setTimeout(connect, 3000)
    }

    ws.onerror = () => ws.close()
  }, [engagementId])

  useEffect(() => {
    connect()
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
    return () => {
      if (wsRef.current) wsRef.current.close()
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }
  }, [connect])

  const clearEvents = useCallback(() => setEvents([]), [])

  return { events, connected, clearEvents }
}
