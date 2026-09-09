import { Fragment, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

export interface DockPanel {
  id: string
  /** preferred panel to absorb container resizes */
  flexible?: boolean
  /** rendered as a floating window: no wrapper, no divider, not part of the split */
  floating?: boolean
  /** docked width on first render */
  defaultWidth?: number
  minWidth?: number
  node: ReactNode
}

const DEFAULT_WIDTH = 384
const DEFAULT_MIN = 240

/**
 * Split container to the right of the side nav.
 *
 * Every boundary between two adjacent docked panels is a draggable split line,
 * and dragging one only transfers width between those two panels — the panels
 * further away keep their size. The outer edges are not draggable.
 *
 * Exactly one docked panel absorbs container resizes (the declared flexible
 * panel, otherwise the first docked one); its width is therefore implicit, so a
 * drag next to it only has to update the neighbour.
 *
 * A floating panel leaves the split entirely and renders as a fixed window.
 */
export default function PanelDock({ panels }: { panels: DockPanel[] }) {
  const docked = panels.filter(p => !p.floating)
  const floaters = panels.filter(p => p.floating)

  const absorberId = (() => {
    if (docked.length === 0) return null
    return (docked.find(p => p.flexible) ?? docked[0]).id
  })()

  // Widths are remembered across panel open/close cycles (the dock stays mounted).
  const [widths, setWidths] = useState<Record<string, number>>({})
  const wrapRefs = useRef<Record<string, HTMLElement | null>>({})

  const widthOf = (p: DockPanel) => widths[p.id] ?? p.defaultWidth ?? DEFAULT_WIDTH
  const minOf = (p: DockPanel) => p.minWidth ?? DEFAULT_MIN

  const dragRef = useRef<{
    leftId: string
    rightId: string
    sx: number
    leftW: number
    rightW: number
    minLeft: number
    minRight: number
  } | null>(null)

  const startDrag = (e: React.PointerEvent, left: DockPanel, right: DockPanel) => {
    e.preventDefault()
    dragRef.current = {
      leftId: left.id,
      rightId: right.id,
      sx: e.clientX,
      leftW: wrapRefs.current[left.id]?.offsetWidth ?? widthOf(left),
      rightW: wrapRefs.current[right.id]?.offsetWidth ?? widthOf(right),
      minLeft: minOf(left),
      minRight: minOf(right),
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = dragRef.current
      if (!d) return
      // left grows by delta, right shrinks by delta; clamp so neither goes under its min
      const delta = Math.max(Math.min(e.clientX - d.sx, d.rightW - d.minRight), d.minLeft - d.leftW)
      setWidths(prev => {
        const next = { ...prev }
        if (d.leftId !== absorberId) next[d.leftId] = d.leftW + delta
        if (d.rightId !== absorberId) next[d.rightId] = d.rightW - delta
        return next
      })
    }
    const up = () => {
      if (!dragRef.current) return
      dragRef.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [absorberId])

  return (
    <div className="flex-1 flex min-w-0">
      {docked.map((panel, i) => {
        const isAbsorber = panel.id === absorberId
        return (
          <Fragment key={panel.id}>
            {i > 0 && (
              <div
                role="separator"
                aria-orientation="vertical"
                aria-label="拖动调整面板宽度"
                onPointerDown={e => startDrag(e, docked[i - 1], panel)}
                className="group relative z-10 -mx-1 w-2 shrink-0 cursor-col-resize"
              >
                <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-gray-800 transition-colors group-hover:bg-amber-400/70 group-active:bg-amber-500" />
              </div>
            )}
            <div
              ref={el => { wrapRefs.current[panel.id] = el }}
              className={isAbsorber ? 'relative flex min-w-0 flex-1' : 'relative flex shrink-0'}
              style={isAbsorber ? undefined : { width: widthOf(panel), minWidth: minOf(panel) }}
            >
              {panel.node}
            </div>
          </Fragment>
        )
      })}

      {floaters.map(panel => <Fragment key={panel.id}>{panel.node}</Fragment>)}

      {panels.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-xs text-gray-600">
          面板都已关闭 · 点击左侧图标重新打开
        </div>
      )}
    </div>
  )
}