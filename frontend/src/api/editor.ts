import { apiGet, apiPost, apiPut, apiDelete } from "./auth"
import type { EditorMockData, Chapter, CharacterRelation, Character } from "../pages/editor/types"

export interface SyncPayload {
  [entityType: string]: {
    created?: Record<string, unknown>[]
    updated?: Record<string, unknown>[]
    deleted?: string[]
  }
}

export interface SyncResult {
  ok: boolean
  version: number
}

// ============================================================
// Editor data (full load + incremental sync)
// ============================================================

export async function loadEditorData(projectId: string): Promise<EditorMockData> {
  const raw: Record<string, unknown[]> = await apiGet(`/api/projects/${projectId}/editor-data`)
  const data = normalizeApiData(raw)
  try {
    const contents = await loadAllSceneContents(projectId)
    for (const ch of data.chapters) {
      for (const sc of ch.scenes) {
        if (contents[sc.id]) sc.content = contents[sc.id]
      }
    }
  } catch {
    // Scene content is best-effort; preview/export will show empty if unavailable
  }
  return data
}

export async function syncEditorData(projectId: string, changes: SyncPayload): Promise<SyncResult> {
  return apiPost(`/api/projects/${projectId}/editor-data/sync`, changes)
}

// ============================================================
// Scene content (lazy-loaded large text)
// ============================================================

export async function loadSceneContent(projectId: string, sceneId: string): Promise<string> {
  const data = await apiGet<{ scene_id: string; content: string }>(`/api/projects/${projectId}/scenes/${sceneId}/content`)
  return data.content
}

export async function loadAllSceneContents(projectId: string): Promise<Record<string, string>> {
  const data = await apiGet<{ contents: Record<string, string> }>(`/api/projects/${projectId}/scenes/content`)
  return data.contents
}

export async function saveSceneContent(projectId: string, sceneId: string, content: string): Promise<{ word_count: number }> {
  return apiPut(`/api/projects/${projectId}/scenes/${sceneId}/content`, { content })
}

export async function aiInline(
  projectId: string,
  sceneId: string,
  action: 'polish' | 'expand' | 'compress',
  selectedText: string,
  fullContent: string,
): Promise<{ result: string }> {
  return apiPost(`/api/projects/${projectId}/scenes/${sceneId}/ai-inline`, {
    action,
    selected_text: selectedText,
    full_content: fullContent,
  })
}

export async function aiContinue(
  projectId: string,
  sceneId: string,
  content: string,
): Promise<{ suggestions: string[] }> {
  return apiPost(`/api/projects/${projectId}/scenes/${sceneId}/ai-continue`, { content })
}

// ============================================================
// Generic entity CRUD
// ============================================================

export async function listEntities(projectId: string, entityType: string): Promise<Record<string, unknown>[]> {
  return apiGet(`/api/projects/${projectId}/${entityType}`)
}

export async function createEntity(projectId: string, entityType: string, data: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiPost(`/api/projects/${projectId}/${entityType}`, data)
}

export async function getEntity(projectId: string, entityType: string, entityId: string): Promise<Record<string, unknown>> {
  return apiGet(`/api/projects/${projectId}/${entityType}/${entityId}`)
}

export async function updateEntity(projectId: string, entityType: string, entityId: string, data: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiPut(`/api/projects/${projectId}/${entityType}/${entityId}`, data)
}

export async function deleteEntity(projectId: string, entityType: string, entityId: string): Promise<{ok: boolean}> {
  return apiDelete(`/api/projects/${projectId}/${entityType}/${entityId}`)
}

// ============================================================
// Normalization: API format → EditorMockData
// ============================================================

function normalizeApiData(raw: Record<string, unknown[]>): EditorMockData {
  const acts = (raw.acts || []) as unknown as ApiAct[]
  const chapters = (raw.chapters || []) as unknown as ApiChapter[]
  const scenes = (raw.scenes || []) as unknown as ApiScene[]
  const edges = (raw.edges || []) as unknown as ApiEdge[]
  const characters = (raw.characters || []) as unknown as ApiCharacter[]
  const charRels = (raw.character_relations || []) as unknown as ApiCharRelation[]
  const themes = (raw.themes || []) as unknown as ApiTheme[]
  const themeChs = (raw.theme_chapters || []) as unknown as ApiThemeChapter[]


  const chMap = new Map<string, Chapter>()
  for (const ch of chapters) {
    chMap.set(ch.id, {
      id: ch.id,
      actId: ch.act_id,
      title: ch.title,
      goal: ch.goal || "",
      wordCount: ch.total_words || 0,
      status: (ch.status as 'draft' | 'revising' | 'final') || 'draft',
      scenes: [],
      sortOrder: ch.sort_order || 0,
    })
  }

  for (const sc of scenes) {
    const ch = chMap.get(sc.chapter_id)
    if (ch) {
      ch.scenes.push({
        id: sc.id,
        chapter_id: sc.chapter_id,
        title: sc.title,
        povCharacter: sc.pov_character || "",
        setting: sc.setting || "",
        time: sc.scene_time || "",
        summary: sc.summary || "",
        content: "",
        wordCount: sc.word_count || 0,
      })
    }
  }

  const charRelMap = new Map<string, CharacterRelation[]>()
  for (const r of charRels) {
    if (!charRelMap.has(r.character_id)) charRelMap.set(r.character_id, [])
    charRelMap.get(r.character_id)!.push({
      id: r.id,
      targetId: r.target_id,
      type: r.rel_type || "关联",
      label: r.label || "",
      description: r.description || "",
      trust: r.trust ?? 50,
      threat: r.threat ?? 50,
      attraction: r.attraction ?? 50,
    })
  }

  const charMap = new Map<string, Character>()
  for (const c of characters) {
    charMap.set(c.id, {
      id: c.id,
      name: c.name,
      role: c.role || "supporting",
      personality: c.personality || "",
      appearance: c.appearance || "",
      background: c.background || "",
      motivation: c.motivation || "",
      relations: charRelMap.get(c.id) || [],
    })
  }

  return {
    projectTitle: (raw.project_title as unknown as string) || "",
    acts: acts.map(a => ({ id: a.id, name: a.name, order: a.sort_order || 0, color: a.color || "#8b5cf6" })),
    chapters: Array.from(chMap.values()),
    edges: edges.map(e => ({
      id: e.id,
      sourceId: e.source_id,
      targetId: e.target_id,
      type: (e.edge_type as "timeline" | "causal" | "foreshadow" | "character" | "theme") || "timeline",
      label: e.label || "",
      sourceHandle: e.source_handle || undefined,
      targetHandle: e.target_handle || undefined,
    })),
    characters: Array.from(charMap.values()),
    rhythms: (raw.rhythms || [])
      .map((r: any) => {
        const chIdx = chapters.findIndex((c: any) => c.id === r.chapter_id)
        return {
          id: r.id,
          chapterId: r.chapter_id,
          chapterIndex: chIdx >= 0 ? chIdx : 0,
          label: `${chIdx >= 0 ? `第${chIdx + 1}章` : '?'}`,
          action: r.action,
          suspense: r.suspense,
          emotion: r.emotion,
          humor: r.humor,
          intensity: r.intensity,
        }
      })
      .sort((a, b) => a.chapterIndex - b.chapterIndex),
    themes: themes.map((t) => ({
      id: t.id,
      name: t.name,
      color: t.color || "#d4a373",
      proposition: t.proposition || "",
      chapterIndices: (() => {
        const chapterIndexMap = new Map<string, number>()
        let j = 0
        for (const key of chMap.keys()) {
          chapterIndexMap.set(key, j)
          j++
        }
        return themeChs.filter(tc => tc.theme_id === t.id).map(tc => {
          return chapterIndexMap.get(tc.chapter_id) ?? 0
        })
      })(),
      connections: [],
    })),
    globalSettings: (raw.global_settings as unknown as string) || "",
  }
}

// ============================================================
// API type interfaces
// ============================================================

interface ApiAct { id: string; name: string; sort_order: number; color: string }
interface ApiChapter { id: string; act_id: string; title: string; goal: string; status: string; sort_order: number; scene_count: number; total_words: number }
interface ApiScene { id: string; chapter_id: string; title: string; sort_order: number; pov_character: string; setting: string; scene_time: string; summary: string; word_count: number }
interface ApiEdge { id: string; source_id: string; target_id: string; edge_type: string; label: string; source_handle: string; target_handle: string }
interface ApiCharacter { id: string; name: string; role: string; personality: string; appearance: string; background: string; motivation: string }
interface ApiCharRelation { id: string; character_id: string; target_id: string; rel_type: string; label: string; description: string; trust: number; threat: number; attraction: number }
interface ApiTheme { id: string; name: string; color: string; proposition: string; note?: string; sort_order?: number }
interface ApiThemeChapter { theme_id: string; chapter_id: string }

