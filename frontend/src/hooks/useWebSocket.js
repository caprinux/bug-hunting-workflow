import { useState, useEffect, useRef, useCallback } from 'react'

export function useWebSocket(engagementId = null) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)

  const connect = useCallback(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = engagementId
      ? `${protocol}//${window.location.host}/ws?engagement_id=${engagementId}`
      : `${protocol}//${window.location.host}/ws`

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

        if (data.type === 'completion') {
          if ('Notification' in window && Notification.permission === 'granted') {
            new Notification('Bug Hunting Workflow', {
              body: `Run completed: ${data.data?.confirmed_bugs || 0} bugs confirmed`,
            })
          }
        }
      } catch (e) {
        console.error('WebSocket parse error:', e)
      }
    }

    ws.onclose = () => {
      setConnected(false)
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
