import { useState, useEffect } from 'react'

/**
 * Live elapsed time display. Shows time since `startTime` (ISO string),
 * updating every second while active.
 */
export default function ElapsedTimer({ startTime, active = true }) {
  const [elapsed, setElapsed] = useState('')

  useEffect(() => {
    if (!startTime) return
    function update() {
      const start = new Date(startTime).getTime()
      const diff = Math.floor((Date.now() - start) / 1000)
      if (diff < 0) { setElapsed('0s'); return }
      if (diff < 60) { setElapsed(`${diff}s`); return }
      const m = Math.floor(diff / 60)
      const s = diff % 60
      if (m < 60) { setElapsed(`${m}m ${s}s`); return }
      const h = Math.floor(m / 60)
      setElapsed(`${h}h ${m % 60}m`)
    }
    update()
    if (!active) return
    const interval = setInterval(update, 1000)
    return () => clearInterval(interval)
  }, [startTime, active])

  if (!startTime) return null
  return <span className="elapsed-timer">{elapsed}</span>
}
