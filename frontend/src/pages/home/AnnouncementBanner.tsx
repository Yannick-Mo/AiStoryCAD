import { useState } from "react"
import { X } from "lucide-react"

export default function AnnouncementBanner() {
  const [dismissed, setDismissed] = useState(() => {
    try { return sessionStorage.getItem("storycad_ann_dismissed") === "1" } catch { return false }
  })

  if (dismissed) return null

  function handleDismiss() {
    setDismissed(true)
    try { sessionStorage.setItem("storycad_ann_dismissed", "1") } catch {}
  }

  return (
    <div className="bg-gradient-to-r from-blue-900/40 via-purple-900/30 to-blue-900/40 border-b border-blue-800/30 px-6 py-2.5 flex items-center justify-center gap-2 text-sm text-blue-300 relative">
      <span className="w-1.5 h-1.5 rounded-full bg-yellow-400 animate-pulse shrink-0" />
      <span>
        🎉 <strong>新功能上线：</strong>AI 写作辅助已就绪，试试在章节详情里写「本章要写什么」，或让 AI 直接续写正文吧～
      </span>
      <button onClick={handleDismiss} className="absolute right-4 text-blue-400 hover:text-blue-200 transition-colors">
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}
