import type { SelectionState } from '../../types'

interface PlotToolbarProps {
  selection: SelectionState
  selectedActId: string | null
  onAddAct: () => void
  onAddChapter: () => void
  onDeleteSelected: () => void
  onLayout: () => void
}

/**
 * 情节幕布顶部栏。
 *
 * 「重建主时序线」按钮已删除：顺序（sort_order）就是唯一真相，时序线只是它的
 * 渲染，没有需要重新投影的东西。连线类型的下拉也一并删除——画布现在只画两样
 * 东西（章节链 + 伏笔线），没有可选的边类型。
 */
export default function PlotToolbar({
  selection, selectedActId,
  onAddAct, onAddChapter, onDeleteSelected, onLayout,
}: PlotToolbarProps) {
  const canDelete = selection.type === 'act' || selection.type === 'chapter'

  return (
    <div className="absolute top-3 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1.5 bg-gray-900/90 backdrop-blur-lg border border-gray-700/50 rounded-xl px-3 py-1.5 shadow-xl">
      <button onClick={onAddAct} className="px-2.5 py-1 rounded-lg text-xs text-gray-300 hover:bg-amber-600/20 hover:text-amber-400 transition-colors" title="添加幕">＋幕</button>
      <button
        onClick={onAddChapter}
        disabled={!selectedActId}
        className={`px-2.5 py-1 rounded-lg text-xs transition-colors ${selectedActId ? 'text-gray-300 hover:bg-amber-600/20 hover:text-amber-400' : 'text-gray-600 cursor-not-allowed'}`}
        title={selectedActId ? '在选中的幕里添加一章' : '先选中一幕'}
      >＋章</button>

      {canDelete && (
        <>
          <div className="w-px h-5 bg-gray-700/50 mx-1" />
          <button onClick={onDeleteSelected} className="px-2.5 py-1 rounded-lg text-xs text-red-400 hover:bg-red-600/20 transition-colors">
            ✕ {selection.type === 'act' ? '删除幕' : '删除章'}
          </button>
        </>
      )}

      <div className="w-px h-5 bg-gray-700/50 mx-1" />
      <button onClick={onLayout} className="px-2.5 py-1 rounded-lg text-xs text-gray-300 hover:bg-gray-700 transition-colors">◉ 布局</button>
    </div>
  )
}
