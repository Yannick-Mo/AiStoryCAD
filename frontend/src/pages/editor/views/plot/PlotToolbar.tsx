import type { SelectionState, EdgeType } from '../../types'

interface PlotToolbarProps {
  selection: SelectionState
  selectedActId: string | null
  connectionMode: 'all' | EdgeType
  onConnectionModeChange: (mode: 'all' | EdgeType) => void
  onAddAct: () => void
  onAddChapter: () => void
  onDeleteSelected: () => void
  onLayout: () => void
}

const MODE_OPTIONS: { value: 'all' | EdgeType; label: string }[] = [
  { value: 'all', label: '全部连线' },
  { value: 'timeline', label: '时序主线' },
  { value: 'causal', label: '因果关系' },
  { value: 'foreshadow', label: '伏笔照应' },
  { value: 'character', label: '人物关联' },
]

export default function PlotToolbar({
  selection, selectedActId, connectionMode,
  onAddAct, onAddChapter, onDeleteSelected,
  onConnectionModeChange, onLayout,
}: PlotToolbarProps) {
  const show = selection.type

  return (
    <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 bg-gray-900/90 backdrop-blur-lg border border-gray-700/50 rounded-xl px-3 py-1.5 shadow-xl">
      <button onClick={onAddAct} className="px-2.5 py-1 rounded-lg text-xs text-gray-300 hover:bg-amber-600/20 hover:text-amber-400 transition-colors" title="添加幕">＋幕</button>
      <button
        onClick={onAddChapter}
        disabled={!selectedActId}
        className={`px-2.5 py-1 rounded-lg text-xs transition-colors ${selectedActId ? 'text-gray-300 hover:bg-amber-600/20 hover:text-amber-400' : 'text-gray-600 cursor-not-allowed'}`}
      >＋章</button>

      {show === 'edge' && (<>
        <div className="w-px h-5 bg-gray-700/50 mx-1" />
        <button onClick={onDeleteSelected} className="px-2.5 py-1 rounded-lg text-xs text-red-400 hover:bg-red-600/20 transition-colors">✕ 删除连线</button>
      </>)}

      <div className="w-px h-5 bg-gray-700/50 mx-1" />
      <select value={connectionMode} onChange={e => onConnectionModeChange(e.target.value as any)}
        className="bg-transparent text-xs text-gray-400 border border-gray-700 rounded px-1.5 py-1 outline-none cursor-pointer"
      >
        {MODE_OPTIONS.map(opt => (
          <option key={opt.value} value={opt.value}>{opt.label}</option>
        ))}
      </select>
      <button onClick={onLayout} className="px-2.5 py-1 rounded-lg text-xs text-gray-300 hover:bg-gray-700 transition-colors">◉ 布局</button>
    </div>
  )
}
