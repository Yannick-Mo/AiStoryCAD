import { useState } from 'react'
import { VIEWS } from '../types'

export function useEditorViews() {
  // null means the canvas is closed: nothing is highlighted in the side nav.
  const [activeViewId, setActiveViewId] = useState<string | null>('narrative-plot')

  const activeView = VIEWS.find(v => v.id === activeViewId) ?? null

  return {
    activeView,
    activeViewId,
    switchView: setActiveViewId,
  }
}