import { useCallback } from 'react'
import type { Character, CharacterRelation, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'

export function useCharacters(
  data: { characters: Character[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
  clearSelection: () => void,
) {
  const addCharacter = useCallback((name?: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const newChar: Character = {
      id, name: name ?? `新角色 ${data.characters.length + 1}`,
      role: 'ally', personality: '', appearance: '', background: '', motivation: '', relations: [],
    }
    setData(d => d ? { ...d, characters: [...d.characters, newChar] } : d)
    enqueueChange({ entity: 'characters', op: 'create', data: { id, project_id: projectId, name: newChar.name, role: newChar.role } })
    return newChar
  }, [data, projectId, enqueueChange, setData])

  const deleteCharacter = useCallback((charId: string) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      characters: d.characters.filter(c => c.id !== charId).map(c => ({ ...c, relations: c.relations.filter(r => r.targetId !== charId) })),
    } : d)
    enqueueChange({ entity: 'characters', op: 'delete', id: charId })
    clearSelection()
  }, [data, enqueueChange, clearSelection, setData])

  const updateCharacter = useCallback((id: string, updates: Partial<Pick<Character, 'name' | 'role' | 'personality' | 'appearance' | 'background' | 'motivation'>>) => {
    setData(d => d ? { ...d, characters: d.characters.map(c => c.id === id ? { ...c, ...updates } : c) } : d)
    const backendData: Record<string, unknown> = { id }
    if (updates.name !== undefined) backendData.name = updates.name
    if (updates.role !== undefined) backendData.role = updates.role
    if (updates.personality !== undefined) backendData.personality = updates.personality
    if (updates.appearance !== undefined) backendData.appearance = updates.appearance
    if (updates.background !== undefined) backendData.background = updates.background
    if (updates.motivation !== undefined) backendData.motivation = updates.motivation
    enqueueChange({ entity: 'characters', op: 'update', data: backendData })
  }, [enqueueChange, setData])

  const addRelation = useCallback((sourceId: string, targetId: string) => {
    if (!data) return
    const id = crypto.randomUUID()
    const newRel: CharacterRelation = { id, targetId, type: '关联', label: '', description: '', trust: 50, threat: 50, attraction: 50 }
    setData(d => d ? {
      ...d,
      characters: d.characters.map(c => c.id === sourceId ? { ...c, relations: [...c.relations, newRel] } : c),
    } : d)
    enqueueChange({ entity: 'character_relations', op: 'create', data: { id, project_id: projectId, character_id: sourceId, target_id: targetId, rel_type: '关联' } })
    return newRel
  }, [data, projectId, enqueueChange, setData])

  const deleteRelation = useCallback((characterId: string, relationId: string) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      characters: d.characters.map(c => c.id === characterId ? { ...c, relations: c.relations.filter(r => r.id !== relationId) } : c),
    } : d)
    enqueueChange({ entity: 'character_relations', op: 'delete', id: relationId })
    clearSelection()
  }, [data, enqueueChange, clearSelection, setData])

  const updateRelation = useCallback((id: string, updates: Partial<Pick<CharacterRelation, 'type' | 'label' | 'description'>>) => {
    setData(d => d ? {
      ...d,
      characters: d.characters.map(ch => ({
        ...ch,
        relations: ch.relations.map(r => r.id === id ? { ...r, ...updates } : r),
      })),
    } : d)
    const backendData: Record<string, unknown> = { id }
    if (updates.type !== undefined) backendData.rel_type = updates.type
    if (updates.label !== undefined) backendData.label = updates.label
    if (updates.description !== undefined) backendData.description = updates.description
    enqueueChange({ entity: 'character_relations', op: 'update', data: backendData })
  }, [enqueueChange, setData])

  return { addCharacter, deleteCharacter, updateCharacter, addRelation, deleteRelation, updateRelation }
}
