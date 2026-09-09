import type { ReactNode } from 'react'
import { useResizePanel } from '../../../hooks/useResizePanel'

export default function ResizablePanel({ children }: { children: ReactNode }) {
  const { size, handleMouseDown } = useResizePanel({ initial: 384, min: 280, max: 800 })

  return (
    <div className="absolute right-0 top-0 h-full z-20" style={{ width: size, maxWidth: '100%' }}>
      <div className="flex h-full">
        <div
          className="w-1.5 cursor-col-resize shrink-0 relative group -ml-1.5"
          onMouseDown={handleMouseDown}
        >
          <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-gray-700 group-hover:bg-amber-400/70 group-active:bg-amber-500 transition-colors" />
        </div>
        <div className="flex-1 min-w-0">
          {children}
        </div>
      </div>
    </div>
  )
}
