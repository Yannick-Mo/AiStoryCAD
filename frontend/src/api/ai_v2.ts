import { getToken } from './auth'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export interface Conversation {
  id: string
  title: string
  created_at: string
  message_count?: number
}

export interface ChatEvent {
  type: 'conv_id' | 'step' | 'token' | 'done' | 'tool_done' | 'plan' | 'project_updated'
  data: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export interface ConversationDetail {
  id: string
  title: string
  created_at: string
  messages: Message[]
}

export interface SendMessageOptions {
  projectId: string
  message: string
  conversationId: string | null
  onToken: (token: string) => void
  onToolDone?: (data: string) => void
  onPlan?: (plan: PlanPayload) => void
  onStep?: (step: string) => void
  onConvId?: (id: string) => void
  onProjectUpdated?: () => void
  onDone?: () => void
  onError: (error: Error) => void
  onComplete?: () => void
  mode?: string
  contextView?: string
  contextId?: string | null
  signal?: AbortSignal
}

async function authHeaders(): Promise<Record<string, string>> {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function parseError(resp: Response): Promise<string> {
  try {
    const text = await resp.text()
    try {
      const parsed = JSON.parse(text)
      return parsed.detail || parsed.message || text
    } catch {
      return text || `HTTP ${resp.status}`
    }
  } catch {
    return `HTTP ${resp.status}`
  }
}

const HEARTBEAT_TIMEOUT = 90000
const HEARTBEAT_INTERVAL = 10000

export function sendMessage(options: SendMessageOptions): AbortController {
  const controller = new AbortController()

  if (options.signal) {
    options.signal.addEventListener('abort', () => controller.abort(), { once: true })
  }

  const eventHandlers: Record<string, (data: string) => void> = {
    conv_id: (data) => options.onConvId?.(data),
    step: (data) => options.onStep?.(data),
    token: (data) => options.onToken(data),
    done: () => options.onDone?.(),
    tool_done: (data) => options.onToolDone?.(data),
    plan: (data) => {
      try {
        const parsed = JSON.parse(data)
        if (parsed.status === 'awaiting_confirmation' && parsed.steps?.length) {
          const steps = parsed.steps.map((s: any) => s.description || s.tool).join('\n')
          options.onToken(`\n\n📋 **计划方案**\n${steps}\n\n**确认执行？**\n`)
          options.onPlan?.(parsed)
        }
      } catch { /* ignore */ }
    },
    project_updated: () => options.onProjectUpdated?.(),
    error: (data: string) => {
      let msg = data
      try {
        const parsed = JSON.parse(data)
        msg = parsed.detail || parsed.message || msg
      } catch { /* plain text */ }
      options.onError(new Error(msg))
    },
  }

  const mode = options.mode ?? 'chat'

  ;(async () => {
    let receivedDone = false

    // Track whether the stream ended with a proper done event
    const origOnDone = options.onDone
    options.onDone = () => {
      receivedDone = true
      origOnDone?.()
    }

    try {
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...(await authHeaders()),
      }

      const resp = await fetch(`${API_BASE}/api/v2/projects/${options.projectId}/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          message: options.message,
          conversation_id: options.conversationId,
          mode,
          context_view: options.contextView,
          context_id: options.contextId,
        }),
        signal: controller.signal,
      })

      if (!resp.ok) {
        throw new Error(await parseError(resp))
      }

      if (!resp.body) {
        throw new Error('Response has no body')
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = ''
      let currentData: string | null = null
      let lastData = Date.now()

      const heartbeat = setInterval(() => {
        if (Date.now() - lastData > HEARTBEAT_TIMEOUT) {
          controller.abort()
          options.onError(new Error('Connection timeout'))
        }
      }, HEARTBEAT_INTERVAL)

      try {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          lastData = Date.now()
          buffer += decoder.decode(value, { stream: true })
          buffer = buffer.replace(/\r\n/g, '\n')
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          for (const line of lines) {
            if (line === '') {
              if (currentEvent && currentData !== null) {
                const handler = eventHandlers[currentEvent]
                if (handler) handler(currentData)
              }
              currentEvent = ''
              currentData = null
            } else if (line.startsWith('event: ')) {
              currentEvent = line.slice(7).trim()
            } else if (line.startsWith('data: ')) {
              const dataLine = line.slice(6)
              currentData = currentData !== null ? currentData + '\n' + dataLine : dataLine
            }
          }
        }

        if (currentEvent && currentData !== null) {
          const handler = eventHandlers[currentEvent]
          if (handler) handler(currentData)
        }
      } finally {
        clearInterval(heartbeat)
      }

      if (!receivedDone) {
        options.onError(new Error('The response may be incomplete - stream ended without a completion signal'))
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        options.onError(err)
      }
    } finally {
      options.onComplete?.()
    }
  })()

  return controller
}

export interface PlanPayload {
  steps: Array<{ tool: string; params: Record<string, unknown>; description: string }>
  status: string
}

export async function getConversations(projectId: string): Promise<{ conversations: Conversation[] }> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/conversations`, {
    headers: await authHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function getConversation(projectId: string, convId: string): Promise<ConversationDetail> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/conversations/${convId}`, {
    headers: await authHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
  return resp.json()
}

export async function renameConversation(projectId: string, convId: string, title: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/conversations/${convId}`, {
    method: 'PATCH',
    headers: { ...(await authHeaders()), 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
}

export async function deleteConversation(projectId: string, convId: string): Promise<void> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/conversations/${convId}`, {
    method: 'DELETE',
    headers: await authHeaders(),
  })
  if (!resp.ok) throw new Error(await parseError(resp))
}

export async function createConversation(projectId: string): Promise<{ conversation_id: string }> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/conversations`, {
    method: 'POST',
    headers: {
      ...(await authHeaders()),
      'Content-Type': 'application/json',
    },
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail = `Failed to create conversation: ${resp.status}`
    try { detail = JSON.parse(text).detail || detail } catch {}
    throw new Error(detail)
  }
  return resp.json()
}

export interface CompressResult {
  compressed: boolean
  detail?: string
  before?: { messages: number; tokens: number }
  after?: { messages: number; tokens: number }
  saved_percent?: number
}

export async function compressContext(projectId: string, conversationId: string): Promise<CompressResult> {
  const resp = await fetch(`${API_BASE}/api/v2/projects/${projectId}/chat/compress`, {
    method: 'POST',
    headers: {
      ...(await authHeaders()),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ conversation_id: conversationId }),
  })
  if (!resp.ok) {
    const text = await resp.text()
    let detail = `Compress failed: ${resp.status}`
    try { detail = JSON.parse(text).detail || detail } catch {}
    throw new Error(detail)
  }
  return resp.json()
}
