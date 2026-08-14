// View system
export interface ViewDef {
  id: string
  label: string
  icon: string
}

export const VIEWS: ViewDef[] = [
  { id: 'narrative-plot', label: '情节', icon: '🎬' },
  { id: 'narrative-char', label: '人物', icon: '👥' },
  { id: 'narrative-rhythm', label: '节奏', icon: '📈' },
  { id: 'narrative-theme', label: '主题', icon: '🎭' },
]

// Edge types
export type EdgeType = 'timeline' | 'causal' | 'foreshadow' | 'character' | 'theme'

export interface ChapterEdge {
  id: string
  sourceId: string
  targetId: string
  type: EdgeType
  label?: string
  sourceHandle?: string
  targetHandle?: string
}

export interface SelectionState {
  type: 'act' | 'chapter' | 'edge' | null
  id: string | null
}

export interface Toast {
  id: string
  message: string
  type: 'info' | 'warning' | 'error' | 'success'
  duration?: number
}

export interface EdgeResult {
  edge: ChapterEdge | null
  cycle?: boolean
}

// Mock data types
export interface Act {
  id: string
  name: string
  order: number
  color: string
  width?: number
  height?: number
}

export interface Scene {
  id: string
  chapter_id: string
  title: string
  povCharacter: string
  setting: string
  time: string
  summary: string
  content: string
  wordCount: number
}

export interface Chapter {
  id: string
  actId: string
  title: string
  goal: string
  wordCount: number
  status: 'draft' | 'revising' | 'final'
  scenes: Scene[]
  sortOrder?: number
}

export interface CharacterRelation {
  id: string
  targetId: string
  type: string
  label: string
  description: string
  trust: number
  threat: number
  attraction: number
}

export interface Character {
  id: string
  name: string
  role: string
  personality: string
  appearance: string
  background: string
  motivation: string
  relations: CharacterRelation[]
}

export interface RhythmPoint {
  id: string
  chapterIndex: number
  intensity: number
  label: string
  action: number
  suspense: number
  emotion: number
  humor: number
  chapterId: string
}

export interface ThemeItem {
  id: string
  name: string
  color: string
  proposition: string
  chapterIndices: number[]
  connections: string[]
  note?: string
}

export interface EditorMockData {
  projectTitle: string
  acts: Act[]
  chapters: Chapter[]
  edges: ChapterEdge[]
  characters: Character[]
  rhythms: RhythmPoint[]
  themes: ThemeItem[]
  globalSettings: string
}

// React Flow node data types
export interface ChapterNodeData {
  actId: string
  actColor: string
  title: string
  goal: string
  wordCount: number
  status: 'draft' | 'revising' | 'final'
  sceneCount: number
  orderBadge?: string | number
}

export interface CharacterNodeData {
  name: string
  role: string
  relations: CharacterRelation[]
}

export interface ThemeNodeData {
  name: string
  color: string
  connections: string[]
}
