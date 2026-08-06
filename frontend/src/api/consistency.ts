import { getToken } from './auth'

const API_BASE = '/api'

export interface ConsistencyIssue {
  check_type: string
  severity: string
  entity_type: string
  entity_id: string | null
  description: string
  suggestion: string | null
  chapter_id: string | null
  scene_id: string | null
}

export interface ConsistencyReport {
  project_id: string
  issues: ConsistencyIssue[]
  summary: string
  timestamp: string | null
}

export interface ConsistencyJobProgress {
  done: number
  total: number
}

export interface ConsistencyJob {
  job_id: string
  project_id: string
  state: 'running' | 'done' | 'failed'
  stage: string
  progress: ConsistencyJobProgress
  message: string
  report: ConsistencyReport | null
  error: string | null
  created_at: string
  finished_at: string | null
}

export interface ReportRecord {
  id: string
  summary: string
  stats: Record<string, number>
  meta: Record<string, unknown>
  created_at: string | null
}

async function request(path: string, init: RequestInit = {}) {
  const token = getToken()
  const resp = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers || {}),
    },
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail = `API error: ${resp.status}`
    try { detail = JSON.parse(text).detail || detail } catch { /* keep default */ }
    throw new Error(detail)
  }
  return resp
}

/**
 * Start a consistency check. Returns either a finished report (fast path:
 * `sync=true` or the project is unchanged since the last report) or a job
 * descriptor to follow over SSE/polling.
 */
export async function checkConsistency(projectId: string, opts: { sync?: boolean; force?: boolean } = {}): Promise<ConsistencyReport | { job_id: string; state: string; reusing: boolean }> {
  const params = new URLSearchParams()
  if (opts.sync) params.set('sync', 'true')
  if (opts.force) params.set('force', 'true')
  const qs = params.toString()
  const resp = await request(`/consistency/projects/${projectId}/check${qs ? `?${qs}` : ''}`, { method: 'POST' })
  return resp.json()
}

export async function getConsistencyJob(jobId: string): Promise<ConsistencyJob> {
  const resp = await request(`/consistency/jobs/${jobId}`)
  return resp.json()
}

/**
 * Subscribe to a job's SSE event stream. `onProgress` / `onDone` / `onError`
 * are invoked with the parsed event data. Resolves when the stream closes.
 */
export async function watchConsistencyJob(
  jobId: string,
  handlers: {
    onProgress?: (data: { stage: string; progress: ConsistencyJobProgress; message: string }) => void
    onDone?: (data: { report: ConsistencyReport }) => void
    onError?: (data: { message: string }) => void
  },
): Promise<void> {
  const token = getToken()
  const resp = await fetch(`${API_BASE}/consistency/jobs/${jobId}/events`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!resp.ok || !resp.body) throw new Error(`SSE error: ${resp.status}`)

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const onEvent = (name: string, raw: string) => {
    if (!raw.trim()) return
    let data: Record<string, unknown> = {}
    try { data = JSON.parse(raw) } catch { return }
    if (name === 'progress') handlers.onProgress?.(data as never)
    else if (name === 'done') handlers.onDone?.(data as never)
    else if (name === 'error') handlers.onError?.(data as never)
  }

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        let eventName = 'message'
        const dataLines: string[] = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
          else if (line.startsWith('retry:')) continue
        }
        onEvent(eventName, dataLines.join('\n'))
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function fetchConsistencyReports(projectId: string): Promise<ReportRecord[]> {
  const resp = await request(`/consistency/projects/${projectId}/reports`)
  return resp.json()
}

export async function resolveConsistencyIssue(issueId: string): Promise<{ ok: boolean; issue_id: string; is_resolved: boolean }> {
  const resp = await request(`/consistency/issues/${issueId}/resolve`, { method: 'POST' })
  return resp.json()
}
