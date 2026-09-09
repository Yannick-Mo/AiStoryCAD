import { useState, useRef, useEffect, useCallback } from 'react'

interface UseResizePanelOptions {
  initial?: number
  min?: number
  max?: number
  direction?: 'horizontal' | 'vertical'
}

export function useResizePanel({
  initial = 380,
  min = 300,
  max = Number.POSITIVE_INFINITY,
  direction = 'horizontal',
}: UseResizePanelOptions = {}) {
  const [size, setSize] = useState(initial)
  const isResizing = useRef(false)
  const startPos = useRef(0)
  const startSize = useRef(0)

  const cursor = direction === 'horizontal' ? 'ew-resize' : 'ns-resize'
  const clientAxis = direction === 'horizontal' ? 'clientX' : 'clientY'

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    isResizing.current = true
    startPos.current = e[clientAxis]
    startSize.current = size
    document.body.style.cursor = cursor
    document.body.style.userSelect = 'none'
  }, [size, clientAxis, cursor])

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing.current) return
      const delta = startPos.current - e[clientAxis]
      const newSize = Math.min(max, Math.max(min, startSize.current + delta))
      setSize(newSize)
    }

    const handleMouseUp = () => {
      if (!isResizing.current) return
      isResizing.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [min, max, clientAxis])

  return { size, handleMouseDown }
}
