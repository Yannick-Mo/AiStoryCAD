import { Fragment, createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { GripVertical } from 'lucide-react'
import { hasVisible, minAlong, movePanel, setWeights } from './splitTree'
import type { DropSide, PanelId, SplitDir, TreeNode } from './splitTree'

export interface DockPanelMeta {
  /** the panel element itself (it owns its own chrome / floating window) */
  node: ReactNode
  /** label used by the drag ghost */
  title: string
  minW: number
  minH: number
}

interface PanelDockProps {
  tree: TreeNode
  onTreeChange: (tree: TreeNode) => void
  panels: Record<PanelId, DockPanelMeta | undefined>
  floating: Record<PanelId, boolean>
}

const DragContext = createContext<{ startPanelDrag: (e: React.PointerEvent, id: PanelId) => void } | null>(null)

/** grip handle a panel renders in its header to start a reorder / split drag */
export function DockGrip({ id, title = '拖动调整位置' }: { id: PanelId; title?: string }) {
  const ctx = useContext(DragContext)
  if (!ctx) return null
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      onPointerDown={e => ctx.startPanelDrag(e, id)}
      className="flex h-6 w-5 shrink-0 cursor-grab items-center justify-center rounded text-gray-600 transition-colors hover:bg-white/10 hover:text-gray-300 active:cursor-grabbing"
    >
      <GripVertical size={13} strokeWidth={1.8} />
    </button>
  )
}

function sideFromRect(rect: DOMRect, x: number, y: number): DropSide {
  const rx = (x - rect.left) / Math.max(rect.width, 1)
  const ry = (y - rect.top) / Math.max(rect.height, 1)
  const edges: Array<[DropSide, number]> = [
    ['left', rx], ['right', 1 - rx], ['top', ry], ['bottom', 1 - ry],
  ]
  edges.sort((a, b) => a[1] - b[1])
  const [side, distance] = edges[0]
  return distance <= 0.28 ? side : 'center'
}

/**
 * Split container to the right of the side nav.
 *
 * The layout is a tree of splits (see splitTree.ts): every boundary between two
 * siblings is a draggable split line, and dragging it only trades width/height
 * between those two siblings. Panels can be dragged by their grip onto another
 * panel — onto its centre to swap places, onto an edge to split in that
 * direction — which rewrites the tree. Panels that are closed or floating are
 * skipped while rendering but keep their place in the tree.
 */
export default function PanelDock({ tree, onTreeChange, panels, floating }: PanelDockProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const treeRef = useRef(tree)
  treeRef.current = tree

  const isVisible = useCallback((id: PanelId) => !!panels[id] && !floating[id], [panels, floating])
  const minsOf = useCallback((id: PanelId) => ({
    w: panels[id]?.minW ?? 240,
    h: panels[id]?.minH ?? 200,
  }), [panels])

  // ── split line drag ────────────────────────────────────────────────
  const divDrag = useRef<null | {
    path: number[]
    li: number
    ri: number
    dir: SplitDir
    start: number
    lpx: number
    rpx: number
    wPair: number
    minL: number
    minR: number
  }>(null)

  const startDividerDrag = (
    e: React.PointerEvent,
    path: number[],
    li: number,
    ri: number,
    dir: SplitDir,
    wL: number,
    wR: number,
    minL: number,
    minR: number,
  ) => {
    const el = e.currentTarget as HTMLElement
    const leftEl = el.previousElementSibling as HTMLElement | null
    const rightEl = el.nextElementSibling as HTMLElement | null
    if (!leftEl || !rightEl) return
    e.preventDefault()
    divDrag.current = {
      path, li, ri, dir,
      start: dir === 'row' ? e.clientX : e.clientY,
      lpx: dir === 'row' ? leftEl.offsetWidth : leftEl.offsetHeight,
      rpx: dir === 'row' ? rightEl.offsetWidth : rightEl.offsetHeight,
      wPair: wL + wR,
      minL, minR,
    }
    document.body.style.cursor = dir === 'row' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
  }

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = divDrag.current
      if (!d) return
      const pos = d.dir === 'row' ? e.clientX : e.clientY
      const delta = Math.max(Math.min(pos - d.start, d.rpx - d.minR), d.minL - d.lpx)
      const pairPx = d.lpx + d.rpx
      if (pairPx <= 0) return
      // keep the pair's total weight, so hidden siblings keep their share
      const newLeft = ((d.lpx + delta) / pairPx) * d.wPair
      onTreeChange(setWeights(treeRef.current, d.path, { [d.li]: newLeft, [d.ri]: d.wPair - newLeft }))
    }
    const up = () => {
      if (!divDrag.current) return
      divDrag.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [onTreeChange])

  // ── panel drag: reorder / split ────────────────────────────────────
  const [panelDrag, setPanelDrag] = useState<{ id: PanelId; x: number; y: number } | null>(null)
  const [dropTarget, setDropTarget] = useState<null | {
    id: PanelId
    side: DropSide
    rect: { x: number; y: number; w: number; h: number }
  }>(null)
  const panelDragRef = useRef<{ id: PanelId } | null>(null)
  const dropRef = useRef(dropTarget)
  dropRef.current = dropTarget

  const startPanelDrag = useCallback((e: React.PointerEvent, id: PanelId) => {
    e.preventDefault()
    e.stopPropagation()
    panelDragRef.current = { id }
    setPanelDrag({ id, x: e.clientX, y: e.clientY })
    document.body.style.userSelect = 'none'
  }, [])

  useEffect(() => {
    const move = (e: PointerEvent) => {
      const d = panelDragRef.current
      if (!d) return
      setPanelDrag({ id: d.id, x: e.clientX, y: e.clientY })
      const hit = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null
      const leafEl = hit?.closest('[data-leaf-id]') as HTMLElement | null
      if (!leafEl) { setDropTarget(null); return }
      const targetId = leafEl.dataset.leafId
      if (!targetId || targetId === d.id) { setDropTarget(null); return }
      const r = leafEl.getBoundingClientRect()
      setDropTarget({
        id: targetId,
        side: sideFromRect(r, e.clientX, e.clientY),
        rect: { x: r.x, y: r.y, w: r.width, h: r.height },
      })
    }
    const up = () => {
      const d = panelDragRef.current
      if (!d) return
      panelDragRef.current = null
      const target = dropRef.current
      setPanelDrag(null)
      setDropTarget(null)
      document.body.style.userSelect = ''
      if (target) onTreeChange(movePanel(treeRef.current, d.id, target.id, target.side))
    }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [onTreeChange])

  // ── render ─────────────────────────────────────────────────────────
  const renderNode = (node: TreeNode, path: number[]): ReactNode => {
    if (node.kind === 'leaf') {
      const meta = panels[node.id]
      if (!meta || floating[node.id]) return null
      const dimmed = panelDrag?.id === node.id
      return (
        <div
          data-leaf-id={node.id}
          className={`relative flex h-full w-full min-w-0 min-h-0 ${dimmed ? 'opacity-40' : ''}`}
        >
          {meta.node}
        </div>
      )
    }

    const visible = node.children
      .map((child, i) => ({ child, i }))
      .filter(({ child }) => hasVisible(child, isVisible))
    if (visible.length === 0) return null
    if (visible.length === 1) return renderNode(visible[0].child, [...path, visible[0].i])

    const total = visible.reduce((sum, { i }) => sum + (node.weights[i] ?? 0), 0) || 1

    return (
      <div className={`flex h-full w-full min-w-0 min-h-0 ${node.dir === 'row' ? 'flex-row' : 'flex-col'}`}>
        {visible.map(({ child, i }, k) => (
          <Fragment key={i}>
            {k > 0 && (() => {
              const prev = visible[k - 1]
              return (
                <div
                  role="separator"
                  aria-orientation={node.dir === 'row' ? 'vertical' : 'horizontal'}
                  aria-label="拖动调整面板大小"
                  onPointerDown={e => startDividerDrag(
                    e, path, prev.i, i, node.dir,
                    node.weights[prev.i] ?? 0,
                    node.weights[i] ?? 0,
                    minAlong(prev.child, node.dir, minsOf),
                    minAlong(child, node.dir, minsOf),
                  )}
                  className={`group relative z-10 shrink-0 ${node.dir === 'row' ? '-mx-1 w-2 cursor-col-resize' : '-my-1 h-2 cursor-row-resize'}`}
                >
                  <div className={`absolute bg-gray-800 transition-colors group-hover:bg-amber-400/70 group-active:bg-amber-500 ${node.dir === 'row' ? 'inset-y-0 left-1/2 w-px -translate-x-1/2' : 'inset-x-0 top-1/2 h-px -translate-y-1/2'}`} />
                </div>
              )
            })()}
            <div
              className="relative flex min-w-0 min-h-0"
              style={{
                flexGrow: (node.weights[i] ?? 0) / total,
                flexBasis: 0,
                minWidth: node.dir === 'row' ? minAlong(child, 'row', minsOf) : 0,
                minHeight: node.dir === 'col' ? minAlong(child, 'col', minsOf) : 0,
              }}
            >
              {renderNode(child, [...path, i])}
            </div>
          </Fragment>
        ))}
      </div>
    )
  }

  const hasAnyPanel = Object.values(panels).some(Boolean)

  // drop preview, positioned relative to the dock
  let preview: ReactNode = null
  if (dropTarget && rootRef.current) {
    const rr = rootRef.current.getBoundingClientRect()
    let left = dropTarget.rect.x - rr.x
    let top = dropTarget.rect.y - rr.y
    let width = dropTarget.rect.w
    let height = dropTarget.rect.h
    if (dropTarget.side === 'left') width = width / 2
    else if (dropTarget.side === 'right') { left += width / 2; width = width / 2 }
    else if (dropTarget.side === 'top') height = height / 2
    else if (dropTarget.side === 'bottom') { top += height / 2; height = height / 2 }
    preview = (
      <div
        className={`pointer-events-none absolute z-40 rounded-md border-2 border-amber-400/70 bg-amber-400/15 ${dropTarget.side === 'center' ? 'border-dashed' : ''}`}
        style={{ left, top, width, height }}
      />
    )
  }

  return (
    <DragContext.Provider value={{ startPanelDrag }}>
      <div ref={rootRef} data-dock-root className="relative flex flex-1 min-w-0 min-h-0">
        {renderNode(tree, [])}

        {Object.entries(panels).map(([id, meta]) => (
          meta && floating[id] ? <Fragment key={id}>{meta.node}</Fragment> : null
        ))}

        {!hasAnyPanel && (
          <div className="flex flex-1 items-center justify-center text-xs text-gray-600">
            面板都已关闭 · 点击左侧图标重新打开
          </div>
        )}

        {preview}

        {panelDrag && (
          <div
            className="pointer-events-none fixed z-50 flex items-center gap-1.5 rounded-md border border-gray-600 bg-gray-900/95 px-2.5 py-1 text-[11px] text-gray-200 shadow-xl"
            style={{ left: panelDrag.x + 14, top: panelDrag.y + 14 }}
          >
            <GripVertical size={12} strokeWidth={1.8} className="text-gray-500" />
            {panels[panelDrag.id]?.title ?? panelDrag.id}
          </div>
        )}
      </div>
    </DragContext.Provider>
  )
}