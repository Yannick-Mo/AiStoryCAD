import { apiDelete, apiGet, apiPost, apiPut } from "./auth"
import type {
  Act, Chapter, ChapterStatus, CharacterRelation, EditorMockData, SetupStatus,
} from "../pages/editor/types"

export interface SyncPayload {
  [entityType: string]: {
    created?: Record<string, unknown>[]
    updated?: Record<string, unknown>[]
    deleted?: string[]
  }
}

/**
 * 编辑器增量同步的结果。
 *
 * 不再有 ``version``：那是 ``project_versions`` 的编辑器同步游标，既不是版本
 * 也不能回滚，已随该表一起删除。顺序的归一化（``renumbered``）由服务端在同一
 * 次调用里完成。也不再回传 ``timeline_edges``：主时序线是 ``sort_order`` 的渲染，
 * 数据层不存图。
 */
export interface SyncResult {
  ok: boolean
  applied: number
  renumbered: { acts?: number; chapters?: number }
}

// ============================================================
// Editor data (full load + incremental sync)
// ============================================================

/**
 * 整包加载编辑器数据。
 *
 * chapters 里**没有**正文：``chapters.content`` 是延迟列，列表查询永远拖不上它。
 * 正文用 ``loadAllChapterContents`` / ``loadChapterContent`` 单独取。
 */
export async function loadEditorData(projectId: string): Promise<EditorMockData> {
  const raw = await apiGet<Record<string, unknown>>(`/api/projects/${projectId}/editor-data`)
  return normalizeApiData(raw)
}

export async function syncEditorData(projectId: string, changes: SyncPayload): Promise<SyncResult> {
  return apiPost(`/api/projects/${projectId}/editor-data/sync`, changes)
}

// ============================================================
// Chapter content（章就是正文层，正文是延迟加载的大文本）
// ============================================================

export async function loadChapterContent(projectId: string, chapterId: string): Promise<string> {
  const data = await apiGet<{ chapter_id: string; content: string }>(
    `/api/projects/${projectId}/chapters/${chapterId}/content`,
  )
  return data.content ?? ""
}

/** 一次取回全部章的正文，键是章 id。 */
export async function loadAllChapterContents(projectId: string): Promise<Record<string, string>> {
  const data = await apiGet<{ contents: Record<string, string> }>(`/api/projects/${projectId}/chapters/content`)
  return data.contents ?? {}
}

export async function saveChapterContent(
  projectId: string, chapterId: string, content: string,
): Promise<{ word_count: number }> {
  return apiPut(`/api/projects/${projectId}/chapters/${chapterId}/content`, { content })
}

// ============================================================
// Chapter AI assist（选区润色 / 扩写 / 压缩 · 续写方向建议）
// ============================================================

export type InlineAction = 'polish' | 'expand' | 'compress'

/** 对选中的一段正文做润色 / 扩写 / 压缩，只返回结果文本。 */
export async function aiInline(
  projectId: string,
  chapterId: string,
  action: InlineAction,
  selectedText: string,
  fullContent: string,
): Promise<{ result: string }> {
  return apiPost(`/api/projects/${projectId}/chapters/${chapterId}/ai-inline`, {
    action,
    selected_text: selectedText,
    full_content: fullContent,
  })
}

/** 根据当前章内容给出续写方向（服务端最多返回 3 条）。 */
export async function aiContinue(
  projectId: string,
  chapterId: string,
  content: string,
): Promise<{ suggestions: string[] }> {
  return apiPost(`/api/projects/${projectId}/chapters/${chapterId}/ai-continue`, { content })
}

// ============================================================
// Setups（伏笔）——走通用 CRUD 端点，不走增量同步
// ============================================================

export async function createSetup(projectId: string, data: {
  id: string
  note: string
  planted_in: string
  pay_off_in: string | null
  status: SetupStatus
}): Promise<void> {
  await apiPost(`/api/projects/${projectId}/setups`, data)
}

export async function updateSetup(
  projectId: string, setupId: string, data: Record<string, unknown>,
): Promise<void> {
  await apiPut(`/api/projects/${projectId}/setups/${setupId}`, data)
}

export async function deleteSetup(projectId: string, setupId: string): Promise<void> {
  await apiDelete(`/api/projects/${projectId}/setups/${setupId}`)
}

// ============================================================
// Generic entity CRUD
// ============================================================

export async function createEntity(projectId: string, entityType: string, data: Record<string, unknown>): Promise<Record<string, unknown>> {
  return apiPost(`/api/projects/${projectId}/${entityType}`, data)
}

/**
 * 移动一项：插到谁之后 / 插到开头 / 接在末尾。章还能顺便换幕。
 *
 * 顺序规范（规格第 5 节）：移动**只用「插到谁之后」表达**——这里不传、也不该
 * 算裸序号，同层序号由服务端归一化成连续整数。所以别再写 ``{ sort_order }``：
 * ``sort_order`` 已经是服务端独占字段，客户端发过去会被直接忽略。
 */
export interface MoveResult {
  ok: boolean
  id: string
  act_id: string | null
  sort_order: number
  global_order: number | null
}

export async function moveEntity(
  projectId: string,
  entityType: 'acts' | 'chapters',
  entityId: string,
  opts: { afterId?: string; position?: 'start' | 'end'; actId?: string } = {},
): Promise<MoveResult> {
  const body: Record<string, unknown> = {}
  if (opts.afterId) body.after_id = opts.afterId
  if (opts.position) body.position = opts.position
  if (opts.actId) body.act_id = opts.actId
  return apiPost<MoveResult>(`/api/projects/${projectId}/${entityType}/${entityId}/move`, body)
}

// ============================================================
// 单表重读：顺序由服务端算，移动之后按它的结果重读一遍
// ============================================================

export async function loadChapters(projectId: string): Promise<Chapter[]> {
  const rows = await apiGet<ApiChapter[]>(`/api/projects/${projectId}/chapters`)
  return rows.map(apiChapterToUi)
}

// ============================================================
// Normalization: API format → EditorMockData
// ============================================================

function apiActToUi(a: ApiAct): Act {
  return {
    id: a.id,
    name: a.name,
    purpose: a.purpose || "",
    order: a.sort_order || 0,
    color: a.color || "#8b5cf6",
  }
}

function apiChapterToUi(ch: ApiChapter): Chapter {
  return {
    id: ch.id,
    actId: ch.act_id,
    title: ch.title,
    goal: ch.goal || "",
    status: (ch.status as ChapterStatus) || 'draft',
    sortOrder: ch.sort_order || 0,
  }
}

function normalizeApiData(raw: Record<string, unknown>): EditorMockData {
  const acts = (raw.acts ?? []) as ApiAct[]
  const chapters = (raw.chapters ?? []) as ApiChapter[]
  const characters = (raw.characters ?? []) as ApiCharacter[]
  const charRels = (raw.character_relations ?? []) as ApiCharRelation[]
  const setups = (raw.setups ?? []) as ApiSetup[]

  const charRelMap = new Map<string, CharacterRelation[]>()
  for (const r of charRels) {
    if (!charRelMap.has(r.character_id)) charRelMap.set(r.character_id, [])
    charRelMap.get(r.character_id)!.push({
      id: r.id,
      targetId: r.target_id,
      type: r.rel_type || "关联",
      label: r.label || "",
      description: r.description || "",
    })
  }

  return {
    projectTitle: (raw.project_title as string) || "",
    acts: acts.map(apiActToUi),
    chapters: chapters.map(apiChapterToUi),
    characters: characters.map(c => ({
      id: c.id,
      name: c.name,
      role: c.role || "supporting",
      personality: c.personality || "",
      appearance: c.appearance || "",
      background: c.background || "",
      motivation: c.motivation || "",
      arc: c.arc || "",
      relations: charRelMap.get(c.id) || [],
    })),
    setups: setups.map(s => ({
      id: s.id,
      note: s.note || "",
      plantedIn: s.planted_in,
      payOffIn: s.pay_off_in ?? null,
      status: (s.status as SetupStatus) || 'open',
    })),
    globalSettings: (raw.global_settings as string) || "",
    // 正文是延迟列：editor-data 不返回它，由 store 单独取一次。
    chapterContents: {},
  }
}

// ============================================================
// API type interfaces
// ============================================================

interface ApiAct { id: string; name: string; purpose: string; sort_order: number; color: string }
interface ApiChapter { id: string; act_id: string; title: string; goal: string; status: string; sort_order: number }
interface ApiCharacter { id: string; name: string; role: string; personality: string; appearance: string; background: string; motivation: string; arc: string }
interface ApiCharRelation { id: string; character_id: string; target_id: string; rel_type: string; label: string; description: string }
interface ApiSetup { id: string; note: string; planted_in: string; pay_off_in: string | null; status: string }
