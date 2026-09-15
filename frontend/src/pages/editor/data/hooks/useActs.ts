import { useCallback } from 'react'
import type { Act, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'

const COLORS = ['#f97316', '#8b5cf6', '#06b6d4', '#ec4899', '#10b981', '#eab308']

export function useActs(
  data: { acts: Act[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
  clearSelection: () => void,
) {
  const addAct = useCallback((name?: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const maxOrder = data.acts.reduce((m, a) => Math.max(m, a.order), 0)
    const newAct: Act = {
      id, name: name ?? `第 ${data.acts.length + 1} 幕`,
      purpose: '',
      order: maxOrder + 1,
      color: COLORS[data.acts.length % COLORS.length],
    }
    setData(d => d ? { ...d, acts: [...d.acts, newAct] } : d)
    // 不带 sort_order：新行的位置由服务端决定（storycad/repository.py 的 _create_entity
    // 会用 next_sort_order 覆盖客户端传来的值），本地那份 order 只用于乐观渲染。
    enqueueChange({ entity: 'acts', op: 'create', data: { id, project_id: projectId, name: newAct.name, color: newAct.color } })
    return newAct
  }, [data, projectId, enqueueChange, setData])

  const deleteAct = useCallback((actId: string) => {
    if (!data) return
    setData(d => {
      if (!d) return d
      const chapterIds = new Set(d.chapters.filter(c => c.actId === actId).map(c => c.id))
      return {
        ...d,
        acts: d.acts.filter(a => a.id !== actId),
        chapters: d.chapters.filter(c => c.actId !== actId),
        // 伏笔是一对配对引用：埋设章随幕一起没了，这条伏笔也不再"埋在某个地方"，
        // 所以跟着一起删（与后端 ON DELETE CASCADE 的语义一致）。收束章被删时
        // 后端只会把 pay_off_in 置空，本地按同样的规则处理。
        setups: d.setups.filter(s => !chapterIds.has(s.plantedIn)),
      }
    })
    enqueueChange({ entity: 'acts', op: 'delete', id: actId })
    clearSelection()
  }, [data, enqueueChange, clearSelection, setData])

  const updateAct = useCallback((id: string, updates: Partial<Pick<Act, 'name' | 'purpose' | 'color'>>) => {
    setData(d => d ? { ...d, acts: d.acts.map(a => a.id === id ? { ...a, ...updates } : a) } : d)
    enqueueChange({ entity: 'acts', op: 'update', data: { id, ...updates } })
  }, [enqueueChange, setData])

  /**
   * 幕盒子的尺寸**只是前端布局**：``acts`` 表没有 width / height 列
   * （模型只有 name / purpose / sort_order / color）。
   *
   * 所以只改本地状态，不再往同步端点上送这两个字段——以前每次缩放结束都会 POST
   * 一个服务端静默丢掉的 payload（``update_entity`` 只设置模型上存在的属性）。
   * 代价是刷新之后幕盒子回到自动布局尺寸；这是设计如此，不要靠给表加两列来"修"。
   */
  const resizeAct = useCallback((actId: string, width: number, height: number) => {
    setData(d => d ? { ...d, acts: d.acts.map(a => a.id === actId ? { ...a, width, height } : a) } : d)
  }, [setData])

  return { addAct, deleteAct, updateAct, resizeAct }
}
