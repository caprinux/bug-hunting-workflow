import { useEffect, useCallback } from 'react'

/**
 * Auto-refresh data on mount and window focus.
 * Prevents stale data when navigating back to a page after a run completes.
 */
export function useAutoRefresh(fetchFn, deps = []) {
  const stableFetch = useCallback(fetchFn, deps)

  useEffect(() => {
    stableFetch()
    const onFocus = () => stableFetch()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [stableFetch])
}
