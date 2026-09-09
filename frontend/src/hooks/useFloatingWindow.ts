import { useCallback, useEffect, useRef, useState } from 'react'

export interface FloatRect {
  x: number
  y: number
  w: number
  h: number
}

interface UseFloatingWindowOptions {
  /** localStorage key that remembers the window geometry */
  storageKey: string
  /** default size when the window is first floated (px) */
  defaultWidth?: number
  defaultHeight?: number
  /** default top-left corner; receives the resolved width */
  defaultX?: (width: number) => number
  defaultY?: number
  /** minimum size enforced while dragging the corner handle */
  minW?: number
  minH?: number
}

/**
 * "Dock / float" behaviour shared by the AI chat panel, the canvas host and the
 * detail panel: a docked element is an in-flow flex item that squeezes its
 * siblings, a floating one is a fixed, draggable and resizable window whose
 * geometry is remembered in localStorage.
 */
export function useFloatingWindow(options: UseFloatingWindowOptions) {
  const { storageKey, minW = 320, minH = 260 } = options
  const optsRef = useRef(options)
  optsRef.current = options

  const [floating, setFloating] = useState(false)
  const [rect, setRect] = useState<FloatRect | null>(null)

  // Restore the saved geometry once.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(storageKey)
      if (raw) {
        const saved = JSON.parse(raw)
        if (typeof saved?.x === 'number') setRect(saved)
      }
    } catch { /* ignore */ }
  }, [storageKey])

  // Persist while floating.
  useEffect(() => {
    if (!floating || !rect) return
    try { localStorage.setItem(storageKey, JSON.stringify(rect)) } catch { /* ignore */ }
  }, [floating, rect, storageKey])

  // Keep the floating window at least partially visible on viewport changes.
  useEffect(() => {
    if (!floating) return
    const onResize = () => {
      setRect(r => {
        if (!r) return r
        return {
          x: Math.max(0, Math.min(r.x, window.innerWidth - 80)),
          y: Math.max(0, Math.min(r.y, window.innerHeight - 60)),
          w: Math.min(r.w, window.innerWidth),
          h: Math.min(r.h, window.innerHeight),
        }
      })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [floating])

  const enterFloat = useCallback(() => {
    setRect(r => {
      if (r) return r
      const o = optsRef.current
      const w = Math.min(o.defaultWidth ?? 460, window.innerWidth)
      const h = Math.min(o.defaultHeight ?? Math.round(window.innerHeight * 0.78), window.innerHeight)
      return {
        x: o.defaultX ? o.defaultX(w) : Math.max(12, window.innerWidth - w - 16),
        y: o.defaultY ?? 72,
        w,
        h,
      }
    })
    setFloating(true)
  }, [])

  const toggleFloat = useCallback(() => {
    if (floating) setFloating(false)
    else enterFloat()
  }, [floating, enterFloat])

  const dock = useCallback(() => setFloating(false), [])

  // Drag the window by its header (skip interactive children).
  const dragRef = useRef<{ sx: number; sy: number; ox: number; oy: number } | null>(null)
  const headerPointerDown = useCallback((e: React.PointerEvent) => {
    if (!floating) return
    if ((e.target as HTMLElement).closest('button, input, textarea, select')) return
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: rect?.x ?? 0, oy: rect?.y ?? 0 }
    document.body.style.userSelect = 'none'
  }, [floating, rect])

  useEffect(() => {
    if (!floating) return
    const move = (e: PointerEvent) => {
      const d = dragRef.current
      if (!d) return
      setRect(r => {
        if (!r) return r
        const x = Math.max(0, Math.min(d.ox + (e.clientX - d.sx), window.innerWidth - 80))
        const y = Math.max(0, Math.min(d.oy + (e.clientY - d.sy), window.innerHeight - 60))
        return { ...r, x, y }
      })
    }
    const up = () => { dragRef.current = null; document.body.style.userSelect = '' }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [floating])

  // Resize from the bottom-right corner.
  const resizeRef = useRef<{ sx: number; sy: number; ow: number; oh: number } | null>(null)
  const cornerPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault(); e.stopPropagation()
    resizeRef.current = { sx: e.clientX, sy: e.clientY, ow: rect?.w ?? 400, oh: rect?.h ?? 600 }
    document.body.style.userSelect = 'none'
  }, [rect])

  useEffect(() => {
    if (!floating) return
    const move = (e: PointerEvent) => {
      const r = resizeRef.current
      if (!r) return
      const w = Math.min(window.innerWidth, Math.max(minW, r.ow + (e.clientX - r.sx)))
      const h = Math.min(window.innerHeight, Math.max(minH, r.oh + (e.clientY - r.sy)))
      setRect(prev => prev ? { ...prev, w, h } : prev)
    }
    const up = () => { resizeRef.current = null; document.body.style.userSelect = '' }
    document.addEventListener('pointermove', move)
    document.addEventListener('pointerup', up)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
    }
  }, [floating, minW, minH])

  return { floating, rect, toggleFloat, dock, headerPointerDown, cornerPointerDown }
}