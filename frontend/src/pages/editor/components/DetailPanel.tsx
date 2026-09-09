import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useResizePanel } from '../../../hooks/useResizePanel'

// Remember the width across mount cycles, so closing a panel and selecting
// another node does not reset the layout.
let rememberedWidth = 384

/**
 * Shared right-hand display container.
 *
 * It is an in-flow flex item, not an overlay: opening it squeezes the canvas
 * host on its left instead of covering it. The container owns only the width,
 * the resize handle and the common chrome — any panel component can be hosted
 * here as children, so the plot details (act / chapter / edge) and the
 * character details (character / relation) share one right-hand slot.
 */
export default function DetailPanel({ children }: { children: ReactNode }) {
  const { size, handleMouseDown } = useResizePanel({ initial: rememberedWidth, min: 280, max: 800 })

  useEffect(() => { rememberedWidth = size }, [size])

  return (
    <div
      className="relative flex shrink-0 bg-gray-900/95 border-l border-gray-800"
      style={{ width: size, maxWidth: '100%' }}
    >
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="拖动调整右侧面板宽度"
        onMouseDown={handleMouseDown}
        className="group absolute inset-y-0 left-0 z-10 w-1.5 -translate-x-1/2 cursor-col-resize"
      >
        <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-gray-700 transition-colors group-hover:bg-amber-400/70 group-active:bg-amber-500" />
      </div>
      <div className="flex min-w-0 flex-1 flex-col">{children}</div>
    </div>
  )
}