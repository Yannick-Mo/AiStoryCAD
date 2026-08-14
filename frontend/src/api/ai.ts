// frontend/src/api/ai.ts
import { getToken } from './auth'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export interface CreateMaterialRequest {
  title: string
  material: string
}

export interface ProgressEvent {
  step: string
  status: string
  preview?: string
  progress?: string
  project_id?: string
  message?: string
}

export function createFromMaterial(
  request: CreateMaterialRequest,
  onProgress: (event: ProgressEvent) => void,
  onDone: (projectId: string) => void,
  onError: (message: string) => void,
): () => void {
  const token = getToken()
  if (!token) { onError('请先登录'); return () => {} }
  const url = `${API_BASE}/api/projects/create-from-material`
  const controller = new AbortController()

  fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(request),
    signal: controller.signal,
  }).then(async (response) => {
    if (!response.ok) {
      const text = await response.text()
      try {
        const err = JSON.parse(text)
        onError(err.detail || '请求失败')
      } catch {
        onError(text || '请求失败')
      }
      return
    }

    const reader = response.body?.getReader()
    if (!reader) { onError('无法读取响应流'); return }

    const decoder = new TextDecoder()
    let buffer = ''

    let receivedTerminal = false
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data: ProgressEvent = JSON.parse(line.slice(6))
            onProgress(data)
            if (data.step === 'done' && data.project_id) {
              receivedTerminal = true
              onDone(data.project_id)
            }
            if (data.step === 'error') {
              receivedTerminal = true
              onError(data.message || '生成失败')
            }
          } catch {}
        }
      }
    }
    if (!receivedTerminal) {
      onError('连接中断，请重试')
    }
  }).catch((err) => {
    if (err.name !== 'AbortError') {
      onError(err.message || '网络错误')
    }
  })

  return () => controller.abort()
}
