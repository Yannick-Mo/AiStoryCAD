import { Fragment } from 'react'
import type { ReactNode } from 'react'

export interface DockPanel {
  id: string
  /** leading group (left side of the dock) or trailing group (right side) */
  align: 'start' | 'end'
  /** absorbs the leftover width when more than one panel is docked */
  flexible?: boolean
  /** currently rendered as a floating window, so it is not part of the docked flow */
  floating?: boolean
  /** gets solo = "this is the only docked panel in the container" */
  render: (solo: boolean) => ReactNode
}

/**
 * The big container to the right of the side nav. It may be empty, and it can
 * host any number of panels (canvas / detail / AI chat, and future ones).
 *
 * Rules:
 *  - docked panels squeeze each other (rule 1);
 *  - a single docked panel fills the container and is centred when it declares a
 *    max width (rule 2);
 *  - with several docked panels the leftover width goes to the flexible one, or
 *    to a spacer between the leading and trailing groups so the trailing group
 *    stays anchored to the right edge;
 *  - a floating panel leaves the flow entirely and does not count as docked.
 */
export default function PanelDock({ panels }: { panels: DockPanel[] }) {
  const docked = panels.filter(p => !p.floating)
  const soloId = docked.length === 1 ? docked[0].id : null
  const hasFlexible = docked.some(p => p.flexible)
  const hasStart = docked.some(p => p.align === 'start')
  const hasEnd = docked.some(p => p.align === 'end')
  const needsSpacer = !hasFlexible && hasStart && hasEnd

  const nodes: ReactNode[] = []
  let spacerPlaced = false
  for (const panel of panels) {
    if (needsSpacer && panel.align === 'end' && !spacerPlaced) {
      nodes.push(<div key="__dock_spacer" className="flex-1" />)
      spacerPlaced = true
    }
    nodes.push(<Fragment key={panel.id}>{panel.render(soloId === panel.id)}</Fragment>)
  }

  return (
    <div className="flex-1 flex min-w-0">
      {nodes}
      {panels.length === 0 && (
        <div className="flex-1 flex items-center justify-center text-xs text-gray-600">
          面板都已关闭 · 点击左侧图标重新打开
        </div>
      )}
    </div>
  )
}