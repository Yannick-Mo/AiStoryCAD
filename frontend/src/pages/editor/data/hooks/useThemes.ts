import { useCallback } from 'react'
import type { ThemeItem, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'

export function useThemes(
  data: { themes: ThemeItem[]; chapters: { id: string }[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
) {
  const _getChapterId = useCallback((chapterIdx: number): string | null => {
    if (!data || !data.chapters) return null
    const ch = data.chapters[chapterIdx]
    return ch ? ch.id : null
  }, [data])

  const addTheme = useCallback((name?: string, color?: string, proposition?: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const newTheme: ThemeItem = {
      id, name: name ?? `新主题 ${data.themes.length + 1}`, color: color ?? '#d4a373',
      proposition: proposition ?? '', chapterIndices: [], connections: [],
    }
    setData(d => d ? { ...d, themes: [...d.themes, newTheme] } : d)
    enqueueChange({ entity: 'themes', op: 'create', data: { id, project_id: projectId, name: newTheme.name, color: newTheme.color, proposition: newTheme.proposition } })
    return newTheme
  }, [data, projectId, enqueueChange, setData])

  const deleteTheme = useCallback((themeId: string) => {
    if (!data) return
    setData(d => d ? { ...d, themes: d.themes.filter(t => t.id !== themeId) } : d)
    enqueueChange({ entity: 'themes', op: 'delete', id: themeId })
  }, [data, enqueueChange, setData])

  const updateTheme = useCallback((id: string, updates: Partial<Pick<ThemeItem, 'name' | 'color' | 'proposition'>>) => {
    setData(d => d ? { ...d, themes: d.themes.map(t => t.id === id ? { ...t, ...updates } : t) } : d)
    enqueueChange({ entity: 'themes', op: 'update', data: { id, ...updates } })
  }, [enqueueChange, setData])

  const addThemeChapterIndex = useCallback((themeId: string, chapterIdx: number) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      themes: d.themes.map(t => t.id === themeId ? {
        ...t, chapterIndices: [...t.chapterIndices, chapterIdx],
      } : t),
    } : d)
    const chapterId = _getChapterId(chapterIdx)
    enqueueChange({
      entity: 'theme_chapters', op: 'create',
      data: { id: crypto.randomUUID(), project_id: projectId, theme_id: themeId, chapter_id: chapterId ?? '', chapter_index: chapterIdx },
    })
  }, [data, projectId, _getChapterId, enqueueChange, setData])

  const removeThemeChapterIndex = useCallback((themeId: string, chapterIdx: number) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      themes: d.themes.map(t => t.id === themeId ? {
        ...t, chapterIndices: t.chapterIndices.filter(i => i !== chapterIdx),
      } : t),
    } : d)
    const chapterId = _getChapterId(chapterIdx)
    enqueueChange({
      entity: 'theme_chapters', op: 'delete',
      data: { theme_id: themeId, chapter_id: chapterId ?? '', chapter_index: chapterIdx },
    })
  }, [data, _getChapterId, enqueueChange, setData])

  return { addTheme, deleteTheme, updateTheme, addThemeChapterIndex, removeThemeChapterIndex }
}
