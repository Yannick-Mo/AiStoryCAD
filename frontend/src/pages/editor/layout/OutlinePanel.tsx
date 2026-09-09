import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { useFloatingWindow } from '../../../hooks/useFloatingWindow'
import { useSessionState } from '../../../hooks/useSessionState'
import WindowControls from '../components/WindowControls'
import type { Chapter, Act } from '../types'

interface OutlinePanelProps {
  acts: Act[]
  chapters: Chapter[]
  selectedActId?: string | null
  selectedChapterId?: string | null
  /** grip handle that starts a dock reorder / split drag */
  grip?: ReactNode
  /** owned by the dock so it survives the remount when the panel floats */
  floating: boolean
  onFloatingChange: (floating: boolean) => void
  onClose: () => void
  onSelectAct: (id: string) => void
  onSelectChapter: (id: string) => void
}

/**
 * The chapter outline as a dock panel instead of an overlay drawer: it sits at
 * the left of the split container, squeezes the other panels, and can be
 * dragged, split, floated or closed like any other panel.
 */
export default function OutlinePanel({
  acts,
  chapters,
  selectedActId,
  selectedChapterId,
  grip,
  floating,
  onFloatingChange,
  onClose,
  onSelectAct,
  onSelectChapter,
}: OutlinePanelProps) {
  const win = useFloatingWindow({
    storageKey: 'aistorycad_outline_panel_float',
    floating,
    onFloatingChange,
    defaultWidth: 320,
    defaultHeight: Math.min(Math.round(window.innerHeight * 0.8), window.innerHeight - 40),
    defaultX: () => 12,
    minW: 220,
    minH: 240,
  })

  const selectedActRef = useRef<HTMLDivElement | null>(null)
  const selectedChapterRef = useRef<HTMLDivElement | null>(null)
  // survives floating / docking, which remounts the panel
  const [collapsedActs, setCollapsedActs] = useSessionState<Set<string>>('outline.collapsedActs', new Set<string>())

  const toggleAct = (id: string) => {
    setCollapsedActs(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // 选中的章若在折叠的幕里，自动展开该幕
  useEffect(() => {
    if (!selectedChapterId) return
    const ch = chapters.find(c => c.id === selectedChapterId)
    if (!ch) return
    setCollapsedActs(prev => {
      if (!prev.has(ch.actId)) return prev
      const next = new Set(prev)
      next.delete(ch.actId)
      return next
    })
  }, [selectedChapterId, chapters, setCollapsedActs])

  // 把当前选中的幕 / 章滚动到可视区域
  useEffect(() => {
    const el = selectedChapterRef.current ?? selectedActRef.current
    if (el) el.scrollIntoView({ block: 'center' })
  }, [selectedActId, selectedChapterId, collapsedActs])

  return (
    <div
      className={win.floating
        ? 'fixed z-30 flex flex-col overflow-hidden rounded-xl border border-gray-700 bg-gray-900/95 shadow-2xl'
        : 'relative flex h-full w-full flex-col bg-gray-900/95'}
      style={win.floating && win.rect
        ? { left: win.rect.x, top: win.rect.y, width: win.rect.w, height: win.rect.h }
        : undefined}
    >
      {/* Header — also the drag handle when floating */}
      <div
        onPointerDown={win.headerPointerDown}
        className={`flex h-10 shrink-0 items-center justify-between border-b border-gray-800 px-3 ${win.floating ? 'cursor-grab select-none active:cursor-grabbing' : ''}`}
      >
        <div className="flex min-w-0 items-center gap-1">
          {!win.floating && grip}
          <span className="text-[11px] text-gray-500">大纲</span>
        </div>
        <WindowControls floating={win.floating} onToggleFloat={win.toggleFloat} onClose={onClose} />
      </div>

      <div className="min-w-0 flex-1 space-y-3 overflow-y-auto p-3">
        {[...acts].sort((a, b) => a.order - b.order).map(act => {
          const actChs = chapters.filter(c => c.actId === act.id)
          const isActSelected = act.id === selectedActId
          const collapsed = collapsedActs.has(act.id)
          const canCollapse = actChs.length > 0
          const actWordCount = actChs.reduce((sum, c) => sum + c.wordCount, 0)
          return (
            <div key={act.id}>
              <div
                ref={isActSelected ? selectedActRef : null}
                className={`flex items-center gap-1 pl-1 pr-2 py-1.5 rounded-lg transition-colors ${
                  isActSelected ? 'bg-green-500/15' : 'hover:bg-gray-800/60'
                }`}
                style={isActSelected ? { boxShadow: 'inset 2px 0 0 0 #22c55e' } : undefined}
              >
                <button
                  onClick={() => toggleAct(act.id)}
                  disabled={!canCollapse}
                  aria-expanded={!collapsed}
                  aria-label={collapsed ? `展开「${act.name}」` : `折叠「${act.name}」`}
                  title={canCollapse ? (collapsed ? '展开该幕' : '折叠该幕') : '该幕暂无章节'}
                  className={`shrink-0 p-0.5 rounded transition-colors ${
                    canCollapse ? 'text-gray-500 hover:text-gray-200 hover:bg-white/10' : 'text-gray-700 cursor-default'
                  }`}
                >
                  <svg
                    width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    className={`transition-transform duration-150 ${collapsed ? '-rotate-90' : ''}`}
                    aria-hidden="true"
                  >
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>
                <button
                  onClick={() => onSelectAct(act.id)}
                  aria-pressed={isActSelected}
                  title={`选中「${act.name}」`}
                  className="flex-1 min-w-0 flex items-center gap-2 text-left"
                >
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: act.color }} />
                  <span className={`flex-1 min-w-0 truncate text-xs font-medium ${isActSelected ? 'text-green-100 font-semibold' : 'text-gray-400'}`}>
                    {act.name}
                  </span>
                </button>
                <span className="shrink-0 text-[10px] text-gray-500 tabular-nums">
                  {actChs.length} 章{actWordCount > 0 ? ` · ${actWordCount} 字` : ''}
                </span>
              </div>
              {!collapsed && (
                <div className="ml-4 mt-0.5 space-y-0.5">
                  {actChs.map(ch => {
                    const isChapterSelected = ch.id === selectedChapterId
                    return (
                      <div
                        key={ch.id}
                        ref={isChapterSelected ? selectedChapterRef : null}
                        onClick={() => onSelectChapter(ch.id)}
                        className={`px-3 py-2 rounded-lg border-l-2 cursor-pointer transition-colors ${
                          isChapterSelected
                            ? 'bg-amber-500/15 ring-1 ring-amber-500/40'
                            : isActSelected
                            ? 'bg-green-500/10 hover:bg-green-500/20'
                            : 'bg-gray-800/30 hover:bg-gray-700/50'
                        }`}
                        style={{ borderLeftColor: isActSelected ? '#22c55e' : act.color }}
                      >
                        <div className={`text-sm truncate ${
                          isChapterSelected ? 'text-amber-100 font-medium' : isActSelected ? 'text-green-100' : 'text-gray-200'
                        }`}>
                          {ch.title}
                        </div>
                        <div className="flex items-center gap-2 text-[10px] text-gray-500 mt-0.5">
                          <span>{ch.scenes.length} 场</span>
                          <span>{ch.wordCount > 0 ? `${ch.wordCount} 字` : '空'}</span>
                          <span className={`px-1 rounded ${
                            ch.status === 'final' ? 'bg-green-900/30 text-green-500' :
                            ch.status === 'revising' ? 'bg-amber-900/30 text-amber-500' :
                            'bg-gray-800 text-gray-500'
                          }`}>
                            {ch.status === 'draft' ? '草稿' : ch.status === 'revising' ? '修改' : '定稿'}
                          </span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Corner resize handle — floating only */}
      {win.floating && (
        <div
          onPointerDown={win.cornerPointerDown}
          title="拖拽调整大小"
          className="absolute bottom-0 right-0 z-10 h-4 w-4 cursor-nwse-resize"
        >
          <div className="absolute bottom-0.5 right-0.5 h-2 w-2 border-b-2 border-r-2 border-gray-600 transition-colors hover:border-amber-500" />
        </div>
      )}
    </div>
  )
}