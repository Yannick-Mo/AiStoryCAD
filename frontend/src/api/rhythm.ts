import { getToken } from './auth'

const API_BASE = '/api'

export interface RhythmAnalysis {
  chapters: RhythmChapter[]
  genre_comparison: Record<string, unknown>
  overall_assessment: string
  emotion_curve: [number, number][]
  info_density: [number, number][]
  dialogue_ratio: [number, number][]
}

export interface RhythmChapter {
  chapter_id: string
  title: string
  metrics: { action: number; suspense: number; emotion: number; humor: number; intensity: number }
  word_count: number
  anomaly_score: number
  anomaly_label: string | null
  ai_note: string | null
}

export async function analyzeRhythm(projectId: string): Promise<RhythmAnalysis> {
  const token = getToken()
  const resp = await fetch(`${API_BASE}/rhythm/projects/${projectId}/analyze`, {
    credentials: 'include',
    headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
  })
  if (!resp.ok) {
    const text = await resp.text();
    let detail = `API error: ${resp.status}`;
    try { detail = JSON.parse(text).detail || detail; } catch {}
    throw new Error(detail);
  }
  return resp.json()
}
