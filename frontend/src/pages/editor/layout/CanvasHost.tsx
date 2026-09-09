import type { ReactNode } from 'react'
import { useFloatingWindow } from '../../../hooks/useFloatingWindow'
import WindowControls from '../components/WindowControls'

interface CanvasHostProps {
  /** shown in the header when the canvas is floating */
  label: string
  /** the active canvas (plot / character) */
  children: ReactNode
  /** canvas toolbar, positioned inside the canvas area */
  toolbar?: ReactNode
  onClose: () => void
}

/**
 * Hosts the active canvas. Docked (default) it is the flexible left part of the
 * workbench row, so the detail panel squeezes it. Floated it becomes a
 * draggable, resizable window and stops taking part in the squeeze.
 */
export default function CanvasHost({ label, children, toolbar, onClose }: CanvasHostProps) {
  const win = useFloatingWindow({
    storageKey: 'aistorycad_canvas_float',
    defaultWidth: Math.min(1100, Math.max(520, window.innerWidth - 200)),
    defaultHeight: Math.min(Math.round(window.innerHeight * 0.82), window.innerHeight - 40),
    minW: 420,
    minH: 320,
  })

  return (
    <div
      className={win.floating
        ? 'fixed z-30 flex flex-col overflow-hidden rounded-xl border border-gray-700 bg-gray-950 shadow-2xl'
        : 'relative flex min-w-0 flex-1 flex-col'}
      style={win.floating && win.rect
        ? { left: win.rect.x, top: win.rect.y, width: win.rect.w, height: win.rect.h }
        : undefined}
    >
      {win.floating && (
        <div
          onPointerDown={win.headerPointerDown}
          className="flex h-8 shrink-0 cursor-grab select-none items-center justify-between border-b border-gray-800 bg-gray-900/95 px-3 active:cursor-grabbing"
        >
          <span className="text-[11px] text-gray-400">{label}</span>
          <div className="flex items-center gap-1">
            <WindowControls floating onToggleFloat={win.toggleFloat} onClose={onClose} />
          </div>
        </div>
      )}

      <div className="relative min-w-0 flex-1">
        {children}
        {toolbar}
        {!win.floating && (
          <div className="absolute right-3 top-3 z-20 flex items-center gap-1 rounded-lg border border-gray-700/60 bg-gray-900/90 px-1 py-0.5 backdrop-blur">
            <WindowControls floating={false} onToggleFloat={win.toggleFloat} onClose={onClose} />
          </div>
        )}
      </div>

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