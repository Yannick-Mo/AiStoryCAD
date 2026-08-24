import type { ProjectListItem } from "../types/project"

// Local single-user tool: no auth tokens. getToken() kept as a no-op
// compatibility shim for API modules that still reference it.
export function getToken(): string | null {
  return null
}

export async function apiGet<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: 'include' })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `HTTP ${res.status}`
    if (text) {
      try { detail = JSON.parse(text).detail ?? text } catch { detail = text.slice(0, 200) }
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function apiPost<T>(url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "POST",
    credentials: 'include',
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `HTTP ${res.status}`
    if (text) {
      try { detail = JSON.parse(text).detail ?? text } catch { detail = text.slice(0, 200) }
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function apiPut<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PUT",
    credentials: 'include',
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `HTTP ${res.status}`
    if (text) {
      try { detail = JSON.parse(text).detail ?? text } catch { detail = text.slice(0, 200) }
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function apiPatch<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: "PATCH",
    credentials: 'include',
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `HTTP ${res.status}`
    if (text) {
      try { detail = JSON.parse(text).detail ?? text } catch { detail = text.slice(0, 200) }
    }
    throw new Error(detail)
  }
  return res.json()
}

export async function apiDelete<T = { ok: boolean }>(url: string): Promise<T> {
  const res = await fetch(url, {
    method: "DELETE",
    credentials: 'include',
  })
  if (!res.ok) {
    const text = await res.text().catch(() => "")
    let detail = `HTTP ${res.status}`
    if (text) {
      try { detail = JSON.parse(text).detail ?? text } catch { detail = text.slice(0, 200) }
    }
    throw new Error(detail)
  }
  return res.json()
}

// Project CRUD
const BASE_PROJECTS = "/api/projects"

export async function listProjects(page = 1, size = 20, search = "", status = ""): Promise<{ items: ProjectListItem[]; total: number; page: number; size: number }> {
  return apiGet(`${BASE_PROJECTS}?page=${page}&size=${size}&search=${encodeURIComponent(search)}&status=${encodeURIComponent(status)}`)
}

export async function createProject(title: string, description?: string): Promise<any> {
  return apiPost(`${BASE_PROJECTS}`, { title, description: description || "" })
}

export async function deleteProject(id: string): Promise<{ ok: boolean }> {
  return apiDelete(`${BASE_PROJECTS}/${id}`)
}

export async function updateProject(id: string, payload: Partial<{ title: string; description: string; status: string }>): Promise<{ ok: boolean }> {
  return apiPatch(`${BASE_PROJECTS}/${id}`, payload)
}

// Runtime model settings (single-user local tool)
export interface ModelSettings {
  configured: boolean
  main_model: string
  main_base_url: string
  main_api_key: string
  middle_model: string
  fallback_models: string[]
  embedding_base_url: string
  embedding_model: string
  embedding_api_key: string
  embedding_proxy: string
  effective_models: string[]
}

export interface TestResult {
  ok: boolean
  status?: number
  model?: string
  detail?: string
  latency_ms?: number | null
}

export async function getModelSettings(): Promise<ModelSettings> {
  return apiGet("/api/settings/models")
}

export async function updateModelSettings(payload: Partial<ModelSettings>): Promise<ModelSettings> {
  return apiPut("/api/settings/models", payload)
}

export async function testModelConnection(payload: { base_url: string; api_key: string; model: string }): Promise<TestResult> {
  return apiPost("/api/settings/models/test", payload)
}
