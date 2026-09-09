import { useEffect, useState } from 'react'
import type { Chapter, Act } from '../types'

interface PreviewPanelProps {
  chapters: Chapter[]
  acts: Act[]
}

/**
 * Read-only preview of the finished manuscript. It fills the detail panel body
 * instead of opening a modal, and borrows the scene editor's paper-like
 * typography so reading feels like reading the book.
 */
export default function PreviewPanel({ chapters, acts }: PreviewPanelProps) {
  const [index, setIndex] = useState(0)
  const [goalExpanded, setGoalExpanded] = useState(false)
  const chapter = chapters[index]

  useEffect(() => {
    if (chapters.length === 0) {
      setIndex(0)
    } else if (index >= chapters.length) {
      setIndex(chapters.length - 1)
    }
  }, [chapters.length, index])

  // 切换章节时收起章核心
  useEffect(() => {
    setGoalExpanded(false)
  }, [index])

  const prev = () => setIndex(i => Math.max(0, i - 1))
  const next = () => setIndex(i => Math.min(chapters.length - 1, i + 1))

  if (!chapter) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-xs leading-relaxed text-gray-600">
        还没有已完成的内容<br />完成章节的创作与定稿后，正文会出现在这里
      </div>
    )
  }

  const act = acts.find(a => a.id === chapter.actId)
  const prevAct = index > 0 ? acts.find(a => a.id === chapters[index - 1]?.actId) : null
  const showActHeader = act && act !== prevAct

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          {showActHeader && <div className="truncate text-[11px] text-amber-500/70">{act!.name}</div>}
          <h4 className="truncate text-sm font-medium text-gray-100">{chapter.title}</h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">{chapter.scenes.length} 场 · {chapter.wordCount} 字</div>
        </div>
        <span className="shrink-0 rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">{index + 1} / {chapters.length}</span>
      </div>

      {chapter.goal && (
        <div className="shrink-0 border-b border-gray-800/60 bg-gray-900/50 px-4 py-2">
          <div className="mx-auto w-full max-w-[46rem]">
            <div className="rounded-lg border border-gray-800 bg-gray-950/60 px-3 py-2">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-amber-500/60">章核心</span>
                <button
                  onClick={() => setGoalExpanded(!goalExpanded)}
                  aria-expanded={goalExpanded}
                  className="flex shrink-0 items-center gap-1 text-[11px] text-gray-500 transition-colors hover:text-amber-400"
                >
                  {goalExpanded ? '收起 ▲' : '展开 ▼'}
                </button>
              </div>
              <div
                title={goalExpanded ? undefined : chapter.goal}
                className={goalExpanded
                  ? 'mt-1 max-h-[40vh] overflow-y-auto whitespace-pre-wrap pr-1 text-xs leading-relaxed text-gray-400'
                  : 'mt-1 truncate text-xs text-gray-400'}
              >
                {chapter.goal}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* reading surface */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
        <div className="mx-auto w-full max-w-[46rem] px-6 py-6 text-[15px] leading-[1.95] tracking-[0.01em] text-gray-200">
          {chapter.scenes.length > 0 ? chapter.scenes.map((scene, si) => (
            <div key={scene.id}>
              {si > 0 && <hr className="my-5 border-gray-800/70" />}
              {scene.title && (
                <div className="mb-2 text-xs font-medium text-gray-500">—— {scene.title} ——</div>
              )}
              {scene.content ? (
                <div className="whitespace-pre-wrap">{scene.content}</div>
              ) : (
                <div className="italic text-gray-600">（内容待创作）</div>
              )}
            </div>
          )) : (
            <div className="text-gray-600">（暂无场景）</div>
          )}
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-center gap-4 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
        <button onClick={prev} disabled={index === 0} className="rounded-full bg-gray-800 px-4 py-1.5 text-xs text-amber-600 transition-colors hover:bg-gray-700 disabled:cursor-default disabled:opacity-30">◀ 上一章</button>
        <span className="text-[11px] tabular-nums text-gray-500">{index + 1} / {chapters.length}</span>
        <button onClick={next} disabled={index >= chapters.length - 1} className="rounded-full bg-gray-800 px-4 py-1.5 text-xs text-amber-600 transition-colors hover:bg-gray-700 disabled:cursor-default disabled:opacity-30">下一章 ▶</button>
      </div>
    </div>
  )
}