import { useEffect, useRef, useState } from 'react'

interface GlobalSettingsPanelProps {
  initialText: string
  onSave: (text: string) => void
  onClose: () => void
}

const PLACEHOLDER = `在此记录你的世界设定...

例如：
■ 世界观：这是一个剑与魔法的世界，三大种族共存
■ 主要大陆：艾泽拉斯（中部）、卡利姆多（西部）...
■ 势力格局：联盟 vs 部落...
■ 重要年表：第 1 年 - 黑暗之门开启...
■ 魔法体系：分为奥术、自然、神圣、暗影四系...`

/**
 * Global project settings as a detail-panel page instead of a modal: it keeps
 * the same writing surface and status bar as the scene editor.
 */
export default function GlobalSettingsPanel({ initialText, onSave, onClose }: GlobalSettingsPanelProps) {
  const [text, setText] = useState(initialText)
  const savedRef = useRef(initialText)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const textValueRef = useRef(text)
  textValueRef.current = text

  useEffect(() => {
    textRef.current?.focus()
  }, [])

  const dirty = text !== savedRef.current

  const handleClose = () => {
    if (dirty && !window.confirm('有未保存的修改，确定关闭吗？')) return
    onClose()
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (textValueRef.current !== savedRef.current && !window.confirm('有未保存的修改，确定关闭吗？')) return
        onClose()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  const handleSave = () => {
    onSave(text)
    savedRef.current = text
    onClose()
  }

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium text-gray-100">🌐 全局设定</h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">记录世界观、地理、势力、年表等任何参考信息</div>
        </div>
        <span className="shrink-0 rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">{text.length} 字</span>
      </div>

      {/* writing surface */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
        <div className="mx-auto flex min-h-full w-full max-w-[46rem] flex-col px-6 py-6">
          <textarea
            ref={textRef}
            value={text}
            onChange={e => setText(e.target.value)}
            spellCheck={false}
            className="w-full flex-1 resize-none border-0 bg-transparent text-[15px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600"
            placeholder={PLACEHOLDER}
          />
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
        <span className="text-[11px] text-gray-600">
          {dirty
            ? <span className="flex items-center gap-1 text-amber-500/80"><span className="h-1.5 w-1.5 rounded-full bg-amber-500/80" />未保存</span>
            : '已保存'}
        </span>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={handleClose}
            className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-700"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-amber-500"
          >
            保存
          </button>
        </div>
      </div>
    </div>
  )
}