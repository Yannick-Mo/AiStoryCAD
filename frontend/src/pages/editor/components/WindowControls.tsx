import { Maximize2, Minimize2, X } from 'lucide-react'

const BTN = 'flex h-6 w-6 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-white/10 hover:text-gray-200'

/**
 * Dock/float toggle + close, shared by the canvas host and the detail panel.
 * `showClose` is off for the full-panel editors, which carry their own cancel.
 */
export default function WindowControls({
  floating, onToggleFloat, onClose, showClose = true,
}: {
  floating: boolean
  onToggleFloat: () => void
  onClose: () => void
  showClose?: boolean
}) {
  return (
    <>
      <button
        onClick={onToggleFloat}
        title={floating ? '停靠' : '独立窗口'}
        aria-label={floating ? '停靠' : '独立窗口'}
        className={BTN}
      >
        {floating
          ? <Minimize2 size={13} strokeWidth={1.8} />
          : <Maximize2 size={13} strokeWidth={1.8} />}
      </button>
      {showClose && (
        <button onClick={onClose} title="关闭" aria-label="关闭" className={BTN}>
          <X size={14} strokeWidth={1.8} />
        </button>
      )}
    </>
  )
}