import { useCallback } from 'react'
import type { Chapter, EditorMockData } from '../../types'
import type { ChangeEntry } from '../editorStore'
import { orderActChapters } from '../orderUtils'
import { loadChapters, moveEntity } from '../../../../api/editor'

export function useChapters(
  data: { chapters: Chapter[] } | null,
  setData: (updater: (prev: EditorMockData | null) => EditorMockData | null) => void,
  projectId: string,
  enqueueChange: (c: ChangeEntry) => void,
) {
  const addChapter = useCallback((actId: string) => {
    if (!data) throw new Error("Store not initialized")
    const id = crypto.randomUUID()
    const actChapters = data.chapters.filter(c => c.actId === actId)
    const maxOrder = actChapters.reduce((m, c) => Math.max(m, c.sortOrder ?? 0), 0)
    const newCh: Chapter = {
      id, actId,
      title: `第 ${actChapters.length + 1} 章`,
      goal: '', status: 'draft', sortOrder: maxOrder + 1,
    }
    setData(d => d ? { ...d, chapters: [...d.chapters, newCh] } : d)
    // 不带 sort_order：新章接在幕末尾，位置由服务端决定（_create_entity 会用
    // next_sort_order 覆盖客户端传来的值），本地那份 sortOrder 只用于乐观渲染。
    enqueueChange({
      entity: 'chapters', op: 'create',
      data: { id, project_id: projectId, act_id: actId, title: newCh.title },
    })
    return newCh
  }, [data, projectId, enqueueChange, setData])

  const deleteChapter = useCallback((chapterId: string) => {
    if (!data) return
    setData(d => d ? {
      ...d,
      chapters: d.chapters.filter(c => c.id !== chapterId),
      // 埋设章被删 → 伏笔随章删除；收束章被删 → 只是计划没了，伏笔还在（pay_off_in 置空）。
      setups: d.setups
        .filter(s => s.plantedIn !== chapterId)
        .map(s => s.payOffIn === chapterId ? { ...s, payOffIn: null } : s),
      chapterContents: Object.fromEntries(
        Object.entries(d.chapterContents).filter(([id]) => id !== chapterId),
      ),
    } : d)
    enqueueChange({ entity: 'chapters', op: 'delete', id: chapterId })
  }, [data, enqueueChange, setData])

  const updateChapter = useCallback((id: string, updates: Partial<Pick<Chapter, 'title' | 'goal' | 'status' | 'actId'>>) => {
    setData(d => d ? { ...d, chapters: d.chapters.map(c => c.id === id ? { ...c, ...updates } : c) } : d)
    const backendData: Record<string, unknown> = { id }
    if (updates.title !== undefined) backendData.title = updates.title
    if (updates.goal !== undefined) backendData.goal = updates.goal
    if (updates.status !== undefined) backendData.status = updates.status
    // 章能换幕：归属走章自己的 act_id（规格第 5 节"归属可改"）。
    if (updates.actId !== undefined) backendData.act_id = updates.actId
    enqueueChange({ entity: 'chapters', op: 'update', data: backendData })
  }, [enqueueChange, setData])

  /**
   * 幕内上移 / 下移一章。
   *
   * 顺序规范（规格第 5 节）：移动**只用「插到谁之后」表达**。这里既不传也不
   * 自己算裸序号——序号是服务端归一化出来的（``sort_order`` 已经是服务端独占
   * 字段，客户端发过去会被忽略）。所以：只报"插到谁之后"，插完按服务端的结果
   * 重读一遍。以前那套本地算 1..N 再逐条 update 的写法是旧逻辑，已删除。
   */
  const moveChapter = useCallback(async (chapterId: string, direction: -1 | 1) => {
    if (!data) return
    const chapter = data.chapters.find(c => c.id === chapterId)
    if (!chapter) return
    const siblings = orderActChapters(data.chapters.filter(c => c.actId === chapter.actId))
    const index = siblings.findIndex(c => c.id === chapterId)
    const target = index + direction
    if (index < 0 || target < 0 || target >= siblings.length) return

    try {
      if (direction === -1) {
        // 上移 = 插到"前一个"的前面：即插到再前一个之后；没有再前一个就插到开头。
        const anchor = index >= 2 ? siblings[index - 2].id : undefined
        await moveEntity(projectId, 'chapters', chapterId,
          anchor ? { afterId: anchor } : { position: 'start' })
      } else {
        // 下移 = 插到"后一个"之后。
        await moveEntity(projectId, 'chapters', chapterId, { afterId: siblings[index + 1].id })
      }
      const fresh = await loadChapters(projectId)
      setData(d => d ? { ...d, chapters: fresh } : d)
    } catch (err) {
      console.error('移动章节失败', err)
    }
  }, [data, projectId, setData])

  /** 换幕：接在目标幕末尾。源幕与目标幕的序号都由服务端在同一次调用里归一化。 */
  const moveChapterToAct = useCallback(async (chapterId: string, actId: string) => {
    if (!data) return
    const chapter = data.chapters.find(c => c.id === chapterId)
    if (!chapter || chapter.actId === actId) return

    try {
      await moveEntity(projectId, 'chapters', chapterId, { actId, position: 'end' })
      const fresh = await loadChapters(projectId)
      setData(d => d ? { ...d, chapters: fresh } : d)
    } catch (err) {
      console.error('换幕失败', err)
    }
  }, [data, projectId, setData])

  return { addChapter, deleteChapter, updateChapter, moveChapter, moveChapterToAct }
}
