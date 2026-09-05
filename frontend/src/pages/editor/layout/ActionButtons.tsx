import { useState, useRef, useCallback, useEffect } from 'react'

interface ActionButtonsProps {
  onAIChat: () => void
  onInspiration: () => void
}

const DRAG_THRESHOLD = 5

export default function ActionButtons({
  onAIChat, onInspiration
}: ActionButtonsProps) {
  const [aiMenuOpen, setAiMenuOpen] = useState(false)
  const [pos, setPos] = useState(() => ({ x: window.innerWidth - 80, y: window.innerHeight - 200 }))
  const dragging = useRef(false)
  const dragStart = useRef({ x: 0, y: 0 })
  const startPos = useRef({ x: 0, y: 0 })
  const wasDrag = useRef(false)

  const aiItems = [
    { label: '💬 AI 对话', action: onAIChat },
    { label: '✨ 灵感生成', action: onInspiration },
  ]

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (e.button !== 0) return
    dragging.current = true
    wasDrag.current = false
    dragStart.current = { x: e.clientX, y: e.clientY }
    startPos.current = { x: pos.x, y: pos.y }
    e.currentTarget.setPointerCapture(e.pointerId)
  }, [pos])

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return
    const dx = e.clientX - dragStart.current.x
    const dy = e.clientY - dragStart.current.y
    if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) {
      wasDrag.current = true
    }
    if (wasDrag.current) {
      setPos({
        x: Math.min(Math.max(startPos.current.x + dx, 0), window.innerWidth - 72),
        y: Math.min(Math.max(startPos.current.y + dy, 0), window.innerHeight - 56),
      })
    }
  }, [])

  const handlePointerUp = useCallback(() => {
    if (!dragging.current) return
    dragging.current = false
    if (!wasDrag.current) {
      setAiMenuOpen(prev => !prev)
    }
  }, [])

  useEffect(() => {
    const clamp = () => {
      setPos(p => ({
        x: Math.min(Math.max(p.x, 0), window.innerWidth - 56),
        y: Math.min(Math.max(p.y, 0), window.innerHeight - 56),
      }))
    }
    window.addEventListener('resize', clamp)
    return () => window.removeEventListener('resize', clamp)
  }, [])

  return (
    <div
      style={{ left: pos.x, top: pos.y, position: 'fixed', zIndex: 9999 }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      className="relative"
    >
      <button
        className="flex items-center gap-1.5 px-4 py-2.5 rounded-full text-sm bg-gradient-to-r from-amber-700/80 to-amber-600/80 border border-amber-500/50 text-white hover:from-amber-600 hover:to-amber-500 transition-all backdrop-blur-sm shadow-lg shadow-amber-900/20 cursor-grab active:cursor-grabbing select-none"
      >
        🤖 AI
      </button>
      {aiMenuOpen && (
        <>
          <div className="fixed inset-0 z-40" onPointerDown={e => e.stopPropagation()} onClick={() => setAiMenuOpen(false)} />
          <div className="absolute bottom-full mb-2 right-0 z-50 w-44 bg-gray-900/95 border border-gray-700 rounded-xl overflow-hidden shadow-xl backdrop-blur-sm" onPointerDown={e => e.stopPropagation()}>
            {aiItems.map((item) => (
              <button
                key={item.label}
                onClick={() => { setAiMenuOpen(false); item.action() }}
                className="w-full text-left px-4 py-2.5 text-sm text-gray-200 hover:bg-amber-600/20 hover:text-amber-400 transition-colors"
              >
                {item.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
