import type { Act, Chapter, ChapterEdge, EdgeType } from '../../types'

interface EdgeDetailProps {
  edge: ChapterEdge
  chapters: Chapter[]
  acts: Act[]
  onClose: () => void
  onChangeType: (edgeId: string, newType: EdgeType) => void
  onDelete: (edgeId: string) => void
  onUpdateEdge: (id: string, updates: Partial<Pick<ChapterEdge, 'label'>>) => void
}

const EDGE_TYPE_OPTIONS: { value: EdgeType; label: string }[] = [
  { value: 'timeline', label: '时序主线' },
  { value: 'causal', label: '因果关系' },
  { value: 'foreshadow', label: '伏笔照应' },
  { value: 'character', label: '人物关联' },
  { value: 'theme', label: '主题关联' },
]

const EDGE_TITLES: Record<EdgeType, string> = {
  timeline: '时序主线',
  causal: '因果关系',
  foreshadow: '伏笔照应',
  character: '人物关联',
  theme: '主题关联',
}

const STATUS_LABELS: Record<Chapter['status'], string> = {
  draft: '草稿',
  revising: '修改',
  final: '定稿',
}

function unique(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)))
}

function getPovNames(chapter?: Chapter) {
  return unique(chapter?.scenes.map(scene => scene.povCharacter) ?? [])
}

function getAct(acts: Act[], chapter?: Chapter) {
  if (!chapter) return undefined
  return acts.find(act => act.id === chapter.actId)
}

function ChapterCard({ label, chapter, act }: { label: string; chapter?: Chapter; act?: Act }) {
  if (!chapter) {
    return (
      <div className="bg-gray-950/50 border border-red-900/40 rounded-xl p-3">
        <div className="text-[10px] text-red-400 mb-1">{label}</div>
        <div className="text-sm text-gray-400">章节已不存在</div>
      </div>
    )
  }

  return (
    <div className="bg-gray-950/50 border border-gray-800 rounded-xl p-3" style={{ borderLeft: `3px solid ${act?.color ?? '#6b7280'}` }}>
      <div className="text-[10px] text-gray-500 mb-1">{label}</div>
      <div className="text-sm font-medium text-gray-200 truncate">{chapter.title}</div>
      <div className="text-xs text-gray-500 mt-1 line-clamp-2">{chapter.goal || '暂无章节目标'}</div>
      <div className="flex items-center gap-2 mt-2 text-[10px] text-gray-600">
        <span>{act?.name ?? '未分幕'}</span>
        <span>{chapter.scenes.length} 场</span>
        <span>{STATUS_LABELS[chapter.status]}</span>
      </div>
    </div>
  )
}

function TypeSpecificContent({ edge, source, target, onUpdateEdge }: {
  edge: ChapterEdge
  source?: Chapter
  target?: Chapter
  onUpdateEdge: (id: string, updates: Partial<Pick<ChapterEdge, 'label'>>) => void
}) {
  const sourcePovs = getPovNames(source)
  const targetPovs = getPovNames(target)
  const sharedPovs = sourcePovs.filter(name => targetPovs.includes(name))
  const allPovs = unique([...sharedPovs, ...sourcePovs, ...targetPovs])
  const sourceFirstSummary = source?.scenes[0]?.summary
  const targetFirstSummary = target?.scenes[0]?.summary

  const note = edge.label || ''

  if (edge.type === 'foreshadow') {
    return (
      <div className="space-y-3">
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">埋设点</div>
          <p className="text-xs text-gray-400 leading-relaxed">{sourceFirstSummary || source?.goal || '暂无埋设说明'}</p>
        </section>
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">回收点</div>
          <p className="text-xs text-gray-400 leading-relaxed">{targetFirstSummary || target?.goal || '暂无回收说明'}</p>
        </section>
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">伏笔说明</div>
          <textarea
            value={note}
            onChange={e => onUpdateEdge(edge.id, { label: e.target.value })}
            placeholder="说明这里埋下了什么，以及后续如何回收..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>
      </div>
    )
  }

  if (edge.type === 'character') {
    return (
      <div className="space-y-3">
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-2">人物线索</div>
          <div className="flex flex-wrap gap-1.5">
            {allPovs.length > 0 ? allPovs.map(name => (
              <span key={name} className={`px-2 py-1 rounded-full text-[10px] ${sharedPovs.includes(name) ? 'bg-amber-600/20 text-amber-300' : 'bg-gray-700 text-gray-400'}`}>{name}</span>
            )) : <span className="text-xs text-gray-500">暂无 POV 人物信息</span>}
          </div>
        </section>
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">关系变化</div>
          <textarea
            value={note}
            onChange={e => onUpdateEdge(edge.id, { label: e.target.value })}
            placeholder="说明两章之间的人物关系如何变化..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>
      </div>
    )
  }

  if (edge.type === 'theme') {
    return (
      <div className="space-y-3">
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">主题说明</div>
          <textarea
            value={note}
            onChange={e => onUpdateEdge(edge.id, { label: e.target.value })}
            placeholder="说明两章共享、呼应或对照的主题命题..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">目标对照</div>
          <p className="text-xs text-gray-400 leading-relaxed">{source?.goal || '源章节暂无目标'} / {target?.goal || '目标章节暂无目标'}</p>
        </section>
      </div>
    )
  }

  return (
    <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
      <div className="text-[10px] text-gray-500 mb-1">因果内容</div>
      <textarea
        value={note}
        onChange={e => onUpdateEdge(edge.id, { label: e.target.value })}
        placeholder="说明这个事件如何推动后续结果..."
        className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
        rows={3}
      />
    </section>
  )
}

export default function EdgeDetail({ edge, chapters, acts, onClose, onChangeType, onDelete, onUpdateEdge }: EdgeDetailProps) {
  const source = chapters.find(chapter => chapter.id === edge.sourceId)
  const target = chapters.find(chapter => chapter.id === edge.targetId)
  const sourceAct = getAct(acts, source)
  const targetAct = getAct(acts, target)
  const sourceIndex = source ? chapters.findIndex(chapter => chapter.id === source.id) : -1
  const targetIndex = target ? chapters.findIndex(chapter => chapter.id === target.id) : -1
  const distance = sourceIndex >= 0 && targetIndex >= 0 ? Math.abs(targetIndex - sourceIndex) : null
  const structureLabel = source && target ? (source.actId !== target.actId ? '跨幕' : '同幕') : '-'

  return (
    <div className="h-full bg-gray-900/95 backdrop-blur-xl flex flex-col shadow-2xl">
      <div className="p-4 border-b border-gray-800">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div>
            <div className="text-[10px] text-gray-500 mb-1">选中连线</div>
            <h3 className="font-medium text-amber-100">{EDGE_TITLES[edge.type]}</h3>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none">✕</button>
        </div>
        <div className="text-xs text-gray-500 line-clamp-2">
          {source?.title ?? edge.sourceId} → {target?.title ?? edge.targetId}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-2">
          <ChapterCard label={edge.type === 'foreshadow' ? '埋设章节' : '来源章节'} chapter={source} act={sourceAct} />
          <div className="flex items-center text-amber-500 text-sm">→</div>
          <ChapterCard label={edge.type === 'foreshadow' ? '回收章节' : '目标章节'} chapter={target} act={targetAct} />
        </div>

        <div className="grid grid-cols-3 gap-2">
          <div className="bg-gray-800/40 border border-gray-700/50 rounded-lg p-2 text-center">
            <div className="text-xs text-gray-300">{distance === null ? '-' : `跨 ${distance} 章`}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">距离</div>
          </div>
          <div className="bg-gray-800/40 border border-gray-700/50 rounded-lg p-2 text-center">
            <div className="text-xs text-gray-300">{structureLabel}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">结构</div>
          </div>
          <div className="bg-gray-800/40 border border-gray-700/50 rounded-lg p-2 text-center">
            <div className="text-xs text-gray-300">{source && target ? '完整' : '缺失'}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">端点</div>
          </div>
        </div>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">连线标签</div>
          <input
            value={edge.label}
            onChange={e => onUpdateEdge(edge.id, { label: e.target.value })}
            placeholder="显示在画布上的标签文字"
            className="w-full bg-gray-950 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-amber-600"
          />
        </section>

        <TypeSpecificContent edge={edge} source={source} target={target} onUpdateEdge={onUpdateEdge} />

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3 space-y-3">
          <div>
            <label className="text-[10px] text-gray-500 block mb-1">类型</label>
            <select
              value={edge.type}
              onChange={event => onChangeType(edge.id, event.target.value as EdgeType)}
              className="w-full bg-gray-950 border border-gray-700 rounded-lg px-2.5 py-1.5 text-xs text-gray-300 outline-none focus:border-amber-600"
            >
              {EDGE_TYPE_OPTIONS.map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={() => onDelete(edge.id)}
            className="w-full px-3 py-1.5 rounded-lg text-xs text-red-400 hover:bg-red-600/20 transition-colors"
          >
            删除连线
          </button>
        </section>
      </div>
    </div>
  )
}
