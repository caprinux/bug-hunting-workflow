const API_BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...options.headers },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || 'Request failed')
  }
  return res.json()
}

export const api = {
  listEngagements: () => request('/engagements'),
  createEngagement: (data) => request('/engagements', { method: 'POST', body: JSON.stringify(data) }),
  getEngagement: (id) => request(`/engagements/${id}`),
  startRun: (id, data) => request(`/engagements/${id}/runs`, { method: 'POST', body: JSON.stringify(data) }),
  listRuns: (id) => request(`/engagements/${id}/runs`),
  getRun: (engId, runId) => request(`/engagements/${engId}/runs/${runId}`),
  listBugs: (id, status) => request(`/engagements/${id}/bugs${status ? `?status=${status}` : ''}`),
  listChains: (id) => request(`/engagements/${id}/chains`),
  getStageOutput: (engId, runId, stage, path = '') =>
    request(`/engagements/${engId}/runs/${runId}/stages/${stage}/output?path=${encodeURIComponent(path)}`),
  getCumulative: (engId, filename) => request(`/engagements/${engId}/cumulative/${filename}`),
}
