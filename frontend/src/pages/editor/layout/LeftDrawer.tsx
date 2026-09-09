import { useEffect, useRef } from 'react'
import type { Chapter, Act } from '../types'

interface LeftDrawerProps {
  open: boolean
  acts: Act[]
  chapters: Chapter[]
  selectedChapterId?: string | null
  onClose: () => void
  onSelectChapter: (id: string) => void
}

export default function LeftDrawer({ open, acts, chapters, selectedChapterId, onClose, onSelectChapter }: LeftDrawerProps) {
  const selectedRef = useRef<HTMLDivElement | null>(null)

  // Esc 收起大纲
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 展开时把当前选中的章滚动到可视区域
  useEffect(() => {
    if (open && selectedRef.current) {
      selectedRef.current.scrollIntoView({ block: 'center' })
    }
  }, [open, selectedChapterId])

  return (
    <div
      className={`fixed left-0 top-0 h-full w-64 bg-gray-900/95 backdrop-blur-xl border-r border-gray-800 z-30 transition-transform duration-200 shadow-2xl flex flex-col ${
        open ? 'translate-x-0' : '-translate-x-full pointer-events-none'
      }`}
    >
      <div className="flex items-center justify-between gap-2 pl-4 pr-2 py-3 border-b border-gray-800">
        <h3 className="text-amber-600/80 text-xs uppercase tracking-wider truncate">📋 全章节大纲</h3>
        <button
          onClick={onClose}
          title="收起大纲（Esc）"
          aria-label="收起大纲"
          className="shrink-0 flex items-center gap-1 pl-1 pr-2 py-1 rounded-lg text-[11px] text-gray-400 hover:text-gray-100 bg-gray-800/60 hover:bg-gray-700 border border-gray-700/60 transition-colors"
        >
          <svg
            width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>
          收起
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {[...acts].sort((a, b) => a.order - b.order).map(act => {
          const actChs = chapters.filter(c => c.actId === act.id)
          return (
            <div key={act.id}>
              <div className="flex items-center gap-2 mb-1">
                <div className="w-2 h-2 rounded-full" style={{ backgroundColor: act.color }} />
                <span className="text-xs font-medium text-gray-400">{act.name}</span>
              </div>
              <div className="ml-4 space-y-0.5">
                {actChs.map(ch => {
                  const isSelected = ch.id === selectedChapterId
                  return (
                    <div
                      key={ch.id}
                      ref={isSelected ? selectedRef : null}
                      onClick={() => onSelectChapter(ch.id)}
                      className={`px-3 py-2 rounded-lg border-l-2 cursor-pointer transition-colors ${
                        isSelected
                          ? 'bg-amber-500/15 ring-1 ring-amber-500/40'
                          : 'bg-gray-800/30 hover:bg-gray-700/50'
                      }`}
                      style={{ borderLeftColor: act.color }}
                    >
                      <div className={`text-sm truncate ${isSelected ? 'text-amber-100 font-medium' : 'text-gray-200'}`}>
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
            </div>
          )
        })}
      </div>
      <div className="p-3 border-t border-gray-800 text-gray-600 text-[10px] text-center">
        点击章节跳转 · 点「收起」或按 Esc 关闭
      </div>
    </div>
  )
}