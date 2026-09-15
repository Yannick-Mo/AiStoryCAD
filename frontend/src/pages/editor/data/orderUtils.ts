import type { Chapter, Act } from '../types'

/**
 * Chapters in reading order: act order first, then the in-act ``sortOrder``.
 *
 * ``sort_order`` is the single source of truth for order everywhere (outline,
 * canvas, preview, export and the agent's own context).  The chapter chain on
 * the plot canvas is a *rendering* of this order — there is no stored "timeline
 * line" to keep in sync any more, so there is exactly one ordering to reason
 * about.  Ties fall back to the incoming array order, which the API already
 * returns in narrative order.
 */
export function orderChapters(chapters: Chapter[], acts: Act[]): Chapter[] {
  const actIndex = new Map(
    [...acts].sort((a, b) => a.order - b.order).map((act, i) => [act.id, i]),
  )
  return chapters
    .map((chapter, index) => ({ chapter, index }))
    .sort((a, b) => {
      const actA = actIndex.get(a.chapter.actId) ?? Number.MAX_SAFE_INTEGER
      const actB = actIndex.get(b.chapter.actId) ?? Number.MAX_SAFE_INTEGER
      if (actA !== actB) return actA - actB
      const orderA = a.chapter.sortOrder ?? 0
      const orderB = b.chapter.sortOrder ?? 0
      if (orderA !== orderB) return orderA - orderB
      return a.index - b.index
    })
    .map(entry => entry.chapter)
}

/** Chapters of a single act in reading order. */
export function orderActChapters(chapters: Chapter[]): Chapter[] {
  return chapters
    .map((chapter, index) => ({ chapter, index }))
    .sort((a, b) => {
      const orderA = a.chapter.sortOrder ?? 0
      const orderB = b.chapter.sortOrder ?? 0
      if (orderA !== orderB) return orderA - orderB
      return a.index - b.index
    })
    .map(entry => entry.chapter)
}

/**
 * 全局章号（算出来的，不存）= 前序各幕的章数之和 + 幕内序号。
 * 返回 章 id → 全局章号 的映射，供画布徽标和大纲 / 伏笔选择器共用。
 */
export function chapterNumbers(chapters: Chapter[], acts: Act[]): Map<string, number> {
  const ordered = orderChapters(chapters, acts)
  return new Map(ordered.map((ch, i) => [ch.id, i + 1]))
}
