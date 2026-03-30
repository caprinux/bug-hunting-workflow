import { useEffect } from 'react'

export default function useTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} — BHW` : 'BHW — Bug Hunting Workflow'
    return () => { document.title = 'BHW — Bug Hunting Workflow' }
  }, [title])
}
