// View system
export interface ViewDef {
  id: string
  label: string
  icon: string
}

export const VIEWS: ViewDef[] = [
  { id: 'narrative-plot', label: '情节', icon: '🎬' },
  { id: 'narrative-char', label: '人物', icon: '👥' },
]

export type ChapterStatus = 'draft' | 'revising' | 'final'
export type SetupStatus = 'open' | 'paid' | 'dropped'

/**
 * Only acts and chapters can be selected. The edge layer is gone: the chapter
 * chain is derived from ``sortOrder`` at render time and setup lines are derived
 * from ``setups`` rows, so neither is a selectable object.
 */
export interface SelectionState {
  type: 'act' | 'chapter' | null
  id: string | null
}

export interface Toast {
  id: string
  message: string
  type: 'info' | 'warning' | 'error' | 'success'
  duration?: number
}

// ── Domain types（章是最底层，也是正文层）─────────────────────────────
export interface Act {
  id: string
  name: string
  /** 这一幕要干什么 */
  purpose: string
  order: number
  color: string
  width?: number
  height?: number
}

export interface Chapter {
  id: string
  actId: string
  title: string
  /** 本章要写什么（动笔前的计划） */
  goal: string
  /** 本章写成了什么（下一章的前情） */
  result: string
  status: ChapterStatus
  sortOrder?: number
}

/** 伏笔：一对配对引用（埋在哪一章、打算在哪一章收），不是一条线。 */
export interface Setup {
  id: string
  note: string
  plantedIn: string
  payOffIn: string | null
  status: SetupStatus
}

/** 一条记录，不是一张图。信任 / 威胁 / 吸引三个强度值已删除。 */
export interface CharacterRelation {
  id: string
  targetId: string
  type: string
  label: string
  description: string
}

export interface Character {
  id: string
  name: string
  role: string
  personality: string
  appearance: string
  background: string
  motivation: string
  /** 他从哪到哪（设计意图：起点 → 终点） */
  arc: string
  relations: CharacterRelation[]
}

export interface EditorMockData {
  projectTitle: string
  acts: Act[]
  chapters: Chapter[]
  characters: Character[]
  setups: Setup[]
  globalSettings: string
  /** 章的正文，键是章 id。正文是延迟列，单独一次请求取回。 */
  chapterContents: Record<string, string>
}

// React Flow node data types
export interface ChapterNodeData {
  actId: string
  actColor: string
  title: string
  goal: string
  wordCount: number
  status: ChapterStatus
  orderBadge?: string | number
}

export interface CharacterNodeData {
  name: string
  role: string
  relations: CharacterRelation[]
}
