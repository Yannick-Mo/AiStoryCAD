import { useEffect } from 'react'
import type { ReactNode } from 'react'
import { useFloatingWindow } from '../../../hooks/useFloatingWindow'
import WindowControls from './WindowControls'

interface DetailPanelProps {
  /** shown in the panel header */
  label: string
  /** report float state to the dock so it can keep the panel out of the split */
  onFloatChange?: (floating: boolean) => void
  onClose: () => void
  children: ReactNode
}

/**
 * Shared display container for the active detail panel. Its docked width is
 * owned by PanelDock (the split container), so this component only renders the
 * content plus the window chrome. Floated it becomes a draggable, resizable
 * window and leaves the split.
 */
export default function DetailPanel({ label, onFloatChange, onClose, children }: DetailPanelProps) {
  const win = useFloatingWindow({
    storageKey: 'aistorycad_detail_panel_float',
    defaultWidth: 460,
    defaultHeight: Math.min(Math.round(window.innerHeight * 0.8), window.innerHeight - 40),
    defaultX: w => Math.max(12, window.innerWidth - w - 16),
    minW: 280,
    minH: 240,
  })

  useEffect(() => { onFloatChange?.(win.floating) }, [win.floating, onFloatChange])

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