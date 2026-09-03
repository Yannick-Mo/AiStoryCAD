import { useEffect } from "react"
import { Search, Plus, Settings } from "lucide-react"

interface HomeNavbarProps {
  searchQuery: string
  onSearchChange: (val: string) => void
  onCreateClick: () => void
  onSettingsClick: () => void
}

export default function HomeNavbar({ searchQuery, onSearchChange, onCreateClick, onSettingsClick }: HomeNavbarProps) {
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey && e.key === "k") || (e.key === "/" && !["INPUT", "TEXTAREA"].includes((e.target as HTMLElement).tagName))) {
        e.preventDefault()
        document.getElementById("homeSearch")?.focus()
      }
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [])

  return (
    <nav className="sticky top-0 z-50 bg-gray-900/80 backdrop-blur-xl border-b border-gray-800 px-6 h-14 flex items-center gap-4">
      <a href="/" className="flex items-center gap-2 text-blue-400 font-bold text-lg no-underline shrink-0 hover:opacity-85 transition-opacity">
        <svg viewBox="0 0 24 24" fill="currentColor" className="w-6 h-6">
          <path d="M12 2L2 7v10c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V7l-10-5zm0 2.18l7 3.5v6.72c0 4.17-2.69 8.08-7 9.08-4.31-1-7-4.91-7-9.08V7.68l7-3.5z"/>
        </svg>
        AiStoryCAD
      </a>
      <div className="relative flex-1 max-w-xs ml-auto">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500 pointer-events-none" />
        <input
          id="homeSearch"
          type="text"
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="搜索项目..."
          className="w-full pl-9 pr-3 py-2 bg-gray-800 border border-gray-700 rounded-full text-sm text-gray-100 placeholder-gray-500 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all"
        />
      </div>
      <button
        onClick={onCreateClick}
        className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold rounded-full transition-all hover:shadow-lg hover:shadow-blue-600/30 active:scale-95"
      >
        <Plus className="w-4 h-4" />
        新建项目
      </button>
      <button
        onClick={onSettingsClick}
        className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-800 text-gray-400 hover:text-amber-400 hover:bg-gray-700 transition-all"
        title="模型配置"
      >
        <Settings className="w-4 h-4" />
      </button>
    </nav>
  )
}
