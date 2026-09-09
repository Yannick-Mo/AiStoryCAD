import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useResizePanel } from '../../../hooks/useResizePanel'
import { useFloatingWindow } from '../../../hooks/useFloatingWindow'
import WindowControls from './WindowControls'

// Remember the width across mount cycles, so closing a panel and selecting
// another node does not reset the layout.
let rememberedWidth = 384

interface DetailPanelProps {
  /** shown in the panel header */
  label: string
  /** the canvas is closed: the panel takes the freed space instead of a fixed width */
  fill?: boolean
  onClose: () => void
  children: ReactNode
}

/**
 * Shared right-hand display container.
 *
 * Docked (default) it is an in-flow flex item that squeezes the canvas host.
 * Floated it becomes a draggable, resizable window and no longer takes part in
 * the squeeze, so closing the other docked window never changes its size.
 * Any panel component can be hosted here as children.
 */
export default function DetailPanel({ label, fill, onClose, children }: DetailPanelProps) {
  const { size, handleMouseDown } = useResizePanel({ initial: rememberedWidth, min: 280, max: 800 })

  useEffect(() => { rememberedWidth = size }, [size])

  const win = useFloatingWindow({
    storageKey: 'aistorycad_detail_panel_float',
    defaultWidth: 460,
    defaultHeight: Math.min(Math.round(window.innerHeight * 0.8), window.innerHeight - 40),
    defaultX: w => Math.max(12, window.innerWidth - w - 16),
    minW: 280,
    minH: 240,
  })

  const dockedStyle = fill ? undefined : { width: size, maxWidth: '100%' }

  return (
    <div
      className={win.floating
        ? 'fixed z-30 flex flex-col overflow-hidden rounded-xl border border-gray-700 bg-gray-900/95 shadow-2xl'
        : `relative flex flex-col bg-gray-900/95 ${fill ? 'min-w-0 flex-1' : 'shrink-0 border-l border-gray-800'}`}
      style={win.floating && win.rect
        ? { left: win.rect.x, top: win.rect.y, width: win.rect.w, height: win.rect.h }
        : dockedStyle}
    >
      {/* Left-edge resize handle — only when docked with a fixed width */}
      {!win.floating && !fill && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="拖动调整右侧面板宽度"
          onMouseDown={handleMouseDown}
          className="group absolute inset-y-0 left-0 z-10 w-1.5 -translate-x-1/2 cursor-col-resize"
        >
          <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-gray-700 transition-colors group-hover:bg-amber-400/70 group-active:bg-amber-500" />
        </div>
      )}

      {/* Header — also the drag handle when floating */}
      <div
        onPointerDown={win.headerPointerDown}
        className={`flex h-9 shrink-0 items-center justify-between border-b border-gray-800 px-3 ${win.floating ? 'cursor-grab select-none active:cursor-grabbing' : ''}`}
      >
        <span className="text-[11px] text-gray-500">{label}</span>
        <div className="flex items-center gap-1">
          <WindowControls floating={win.floating} onToggleFloat={win.toggleFloat} onClose={onClose} />
        </div>
      </div>

      <div className="flex min-w-0 flex-1 flex-col">{children}</div>

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