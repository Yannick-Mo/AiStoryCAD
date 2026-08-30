import { getToken } from './auth'

const API_BASE = '/api'

export interface StoryStarter {
  title: string
  hook: string
  premise: string
  protagonist: string
  opening_scene: string
  tags: string[]
}

export interface CreativeChallenge {
  title: string
  description: string
  constraints: string[]
  genre: string
  difficulty: string
}

export async function generateStarter(genre: string, style?: string, constraints?: string[]): Promise<StoryStarter> {
  const token = getToken()
  const resp = await fetch(`${API_BASE}/inspiration/starter`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ genre, style, constraints }),
  })
  if (!resp.ok) {
    const text = await resp.text();
    let detail = `API error: ${resp.status}`;
    try { detail = JSON.parse(text).detail || detail; } catch {}
    throw new Error(detail);
  }
  return resp.json()
}

export async function batchGenerate(genres: string[], count?: number): Promise<StoryStarter[]> {
  const token = getToken()
  const resp = await fetch(`${API_BASE}/inspiration/batch`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: JSON.stringify({ genres, count }),
  })
  if (!resp.ok) {
    const text = await resp.text();
    let detail = `API error: ${resp.status}`;
    try { detail = JSON.parse(text).detail || detail; } catch {}
    throw new Error(detail);
  }
  return resp.json()
}

export async function getChallenges(difficulty?: string, genre?: string): Promise<CreativeChallenge[]> {
  const token = getToken()
  const params = new URLSearchParams()
  if (difficulty) params.set('difficulty', difficulty)
  if (genre) params.set('genre', genre)
  const qs = params.toString()
  const resp = await fetch(`${API_BASE}/inspiration/challenges${qs ? `?${qs}` : ''}`, {
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

export async function getRandomChallenge(): Promise<CreativeChallenge> {
  const token = getToken()
  const resp = await fetch(`${API_BASE}/inspiration/challenges/random`, {
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
