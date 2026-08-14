import type { Chapter, Act } from '../types'

interface LeftDrawerProps {
  open: boolean
  acts: Act[]
  chapters: Chapter[]
  onClose: () => void
  onSelectChapter: (id: string) => void
}

export default function LeftDrawer({ open, acts, chapters, onClose, onSelectChapter }: LeftDrawerProps) {
  return (
    <>
      {open && <div className="fixed inset-0 z-20" onClick={onClose} />}
      <div
        className={`fixed left-0 top-0 h-full w-64 bg-gray-900/95 backdrop-blur-xl border-r border-gray-800 z-30 transition-transform duration-200 shadow-2xl flex flex-col ${
          open ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="p-4 border-b border-gray-800">
          <h3 className="text-amber-600/80 text-xs uppercase tracking-wider">📋 全章节大纲</h3>
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
                  {actChs.map(ch => (
                    <div
                      key={ch.id}
                      onClick={() => { onSelectChapter(ch.id); onClose() }}
                      className="px-3 py-2 rounded-lg bg-gray-800/30 border-l-2 hover:bg-gray-700/50 cursor-pointer transition-colors"
                      style={{ borderLeftColor: act.color }}
                    >
                      <div className="text-sm text-gray-200 truncate">{ch.title}</div>
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
                  ))}
                </div>
              </div>
            )
          })}
        </div>
        <div className="p-3 border-t border-gray-800 text-gray-600 text-[10px] text-center">点击跳转章节</div>
      </div>
    </>
  )
}
