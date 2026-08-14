import { useState, useEffect } from 'react'
import type { ThemeItem, Chapter } from '../../types'

interface ThemeDetailProps {
  theme: ThemeItem
  chapter?: Chapter
  onClose: () => void
  onSaveNote: (note: string) => void
}

export default function ThemeDetail({ theme, chapter, onClose, onSaveNote }: ThemeDetailProps) {
  const [note, setNote] = useState(theme.note ?? '')

  useEffect(() => {
    setNote(theme.note ?? '')
  }, [theme.id])

  return (
    <div className="h-full bg-gray-900/95 backdrop-blur-xl flex flex-col shadow-2xl">
      <div className="p-4 border-b border-gray-800" style={{ borderLeft: `3px solid ${theme.color}` }}>
        <div className="flex items-start justify-between gap-3 mb-2">
          <h3 className="font-medium text-lg" style={{ color: theme.color }}>#{theme.name}</h3>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none">✕</button>
        </div>
        {chapter && (
          <div className="text-xs text-gray-500">
            在「{chapter.title}」中的体现
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">主题命题</div>
          <p className="text-xs text-gray-300 leading-relaxed">{theme.proposition}</p>
        </section>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">关联主题</div>
          <div className="flex flex-wrap gap-1.5">
            {theme.connections.length > 0
              ? theme.connections.map(name => (
                  <span key={name} className="px-2 py-1 rounded-full text-[10px] bg-gray-700 text-gray-300">#{name}</span>
                ))
              : <span className="text-xs text-gray-500">暂无关联</span>}
          </div>
        </section>

        {chapter && (
          <>
            <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
              <div className="text-[10px] text-gray-500 mb-2">本章信息</div>
              <div className="text-sm text-gray-200">{chapter.title}</div>
              <div className="text-xs text-gray-500 mt-1">{chapter.goal || '暂无目标'}</div>
              <div className="text-[10px] text-gray-600 mt-1">{chapter.wordCount > 0 ? `${chapter.wordCount} 字` : '未开始写作'}</div>
            </section>

            <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
              <div className="text-[10px] text-gray-500 mb-2">体现说明</div>
              <textarea
                value={note}
                onChange={e => setNote(e.target.value)}
                onBlur={() => { if (note) onSaveNote(note) }}
                placeholder="在此记录该主题如何在本章体现..."
                className="w-full h-24 bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
              />
            </section>
          </>
        )}
      </div>
    </div>
  )
}
