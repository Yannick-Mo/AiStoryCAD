import { Fragment } from 'react'
import type { ReactNode } from 'react'

export interface DockPanel {
  id: string
  /** leading group (left side of the dock) or trailing group (right side) */
  align: 'start' | 'end'
  /** preferred panel to absorb the leftover width (the canvas) */
  flexible?: boolean
  /** currently rendered as a floating window, so it is not part of the docked flow */
  floating?: boolean
  /** gets fill = "this panel absorbs the leftover width" */
  render: (fill: boolean) => ReactNode
}

/**
 * The big container to the right of the side nav. It may be empty, and it can
 * host any number of panels (canvas / detail / AI chat, and future ones).
 *
 * Rules:
 *  - docked panels squeeze each other (rule 1);
 *  - exactly one docked panel absorbs the leftover width, so the dock always
 *    fills its container and neighbouring panels share a draggable boundary
 *    (rule 2). The declared flexible panel wins (the canvas); otherwise the
 *    last leading panel, otherwise the first docked panel;
 *  - a single docked panel is therefore the absorbing one and fills the whole
 *    container;
 *  - a floating panel leaves the flow entirely and does not take part in this.
 */
export default function PanelDock({ panels }: { panels: DockPanel[] }) {
  const docked = panels.filter(p => !p.floating)
  const fillId = (() => {
    if (docked.length === 0) return null
    const declared = docked.find(p => p.flexible)
    if (declared) return declared.id
    const lastLeading = [...docked].reverse().find(p => p.align === 'start')
    return (lastLeading ?? docked[0]).id
  })()

  return (
    <div className="flex-1 flex min-w-0">
      {panels.map(panel => (
        <Fragment key={panel.id}>
          {panel.render(!panel.floating && panel.id === fillId)}
        </Fragment>
      ))}
      {panels.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-xs text-gray-600">
          面板都已关闭 · 点击左侧图标重新打开
        </div>
      )}
    </div>
  )
}