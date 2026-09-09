import type { Character, CharacterRelation } from '../../types'

interface CharacterEdgeDetailProps {
  source: Character
  target: Character
  relation: CharacterRelation
  onClose: () => void
  onDelete: () => void
  onUpdateRelation: (id: string, updates: Partial<Pick<CharacterRelation, 'type' | 'label' | 'description'>>) => void
}

const RELATION_TYPE_OPTIONS = [
  { value: '关联', label: '关联' },
  { value: '盟友', label: '盟友' },
  { value: '敌对', label: '敌对' },
  { value: '恋人', label: '恋人' },
  { value: '师徒', label: '师徒' },
  { value: '亲友', label: '亲友' },
  { value: '上下级', label: '上下级' },
  { value: '竞争对手', label: '竞争对手' },
  { value: '恩怨', label: '恩怨' },
  { value: '其他', label: '其他' },
]

const ROLE_LABELS: Record<string, string> = {
  protagonist: '主角',
  ally: '盟友',
  antagonist: '对手',
}

export default function CharacterEdgeDetail({ source, target, relation, onClose, onDelete, onUpdateRelation }: CharacterEdgeDetailProps) {
  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-gray-800">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex-1">
            <div className="text-[10px] text-gray-500 mb-1">选中关系连线</div>
            <select
              value={relation.type}
              onChange={e => onUpdateRelation(relation.id, { type: e.target.value })}
              className="w-full bg-transparent font-medium text-amber-100 outline-none border-b border-transparent focus:border-amber-600/50"
            >
              {RELATION_TYPE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
              ))}
            </select>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none shrink-0">✕</button>
        </div>
        <div className="text-xs text-gray-500">
          {source.name} → {target.name}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <div className="grid grid-cols-[1fr_auto_1fr] items-stretch gap-2">
          <div className="bg-gray-950/50 border border-gray-800 rounded-xl p-3">
            <div className="text-[10px] text-gray-500 mb-1">源角色</div>
            <div className="text-sm font-medium text-gray-200 truncate">{source.name}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">{ROLE_LABELS[source.role] ?? source.role}</div>
          </div>
          <div className="flex items-center text-amber-500 text-sm">→</div>
          <div className="bg-gray-950/50 border border-gray-800 rounded-xl p-3">
            <div className="text-[10px] text-gray-500 mb-1">目标角色</div>
            <div className="text-sm font-medium text-gray-200 truncate">{target.name}</div>
            <div className="text-[10px] text-gray-600 mt-0.5">{ROLE_LABELS[target.role] ?? target.role}</div>
          </div>
        </div>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">连线标签</div>
          <input
            value={relation.label}
            onChange={e => onUpdateRelation(relation.id, { label: e.target.value })}
            placeholder="显示在画布上的标签文字"
            className="w-full bg-gray-950 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 focus:outline-none focus:border-amber-600"
          />
        </section>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">关系详情</div>
          <textarea
            value={relation.description}
            onChange={e => onUpdateRelation(relation.id, { description: e.target.value })}
            placeholder="描述这两个角色之间的关系..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={4}
          />
        </section>

        <button
          onClick={onDelete}
          className="w-full px-3 py-1.5 rounded-lg text-xs text-red-400 hover:bg-red-600/20 transition-colors"
        >
          删除连线
        </button>
      </div>
    </div>
  )
}
