import type { Character } from '../../types'

interface CharacterDetailProps {
  character: Character
  onUpdateCharacter: (id: string, updates: Partial<Pick<Character, 'name' | 'role' | 'personality' | 'appearance' | 'background' | 'motivation'>>) => void
}

const ROLE_OPTIONS = [
  { value: 'protagonist', label: '主角' },
  { value: 'ally', label: '盟友' },
  { value: 'antagonist', label: '对手' },
  { value: 'supporting', label: '配角' },
  { value: 'mentor', label: '导师' },
  { value: 'love_interest', label: '恋人' },
  { value: 'comic_relief', label: '搞笑' },
  { value: 'other', label: '其他' },
]

export default function CharacterDetail({ character, onUpdateCharacter }: CharacterDetailProps) {
  return (
    <div className="h-full flex flex-col">
      <div className="p-4 border-b border-gray-800">
        <div className="flex items-start justify-between gap-3 mb-1">
          <div className="flex-1 flex items-center gap-2">
            <input
              value={character.name}
              onChange={e => onUpdateCharacter(character.id, { name: e.target.value })}
              className="flex-1 bg-transparent font-medium text-amber-100 text-lg outline-none border-b border-transparent focus:border-amber-600/50"
            />
            <select
              value={character.role}
              onChange={e => onUpdateCharacter(character.id, { role: e.target.value })}
              className="px-2 py-0.5 rounded-full text-[10px] bg-amber-600/20 text-amber-400 outline-none border border-transparent focus:border-amber-600/50"
            >
              {ROLE_OPTIONS.map(opt => (
                <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
              ))}
            </select>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">性格特征</div>
          <textarea
            value={character.personality}
            onChange={e => onUpdateCharacter(character.id, { personality: e.target.value })}
            placeholder="描述角色的性格特征..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">外貌描述</div>
          <textarea
            value={character.appearance}
            onChange={e => onUpdateCharacter(character.id, { appearance: e.target.value })}
            placeholder="角色的外貌特征..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">身世背景</div>
          <textarea
            value={character.background}
            onChange={e => onUpdateCharacter(character.id, { background: e.target.value })}
            placeholder="角色的过去经历..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={4}
          />
        </section>

        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1">核心动机</div>
          <textarea
            value={character.motivation}
            onChange={e => onUpdateCharacter(character.id, { motivation: e.target.value })}
            placeholder="角色的目标和动力..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        {character.relations.length > 0 && (
          <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
            <div className="text-[10px] text-gray-500 mb-2">关系网络</div>
            <div className="space-y-1.5">
              {character.relations.map(rel => (
                <div key={rel.id} className="bg-gray-950/50 border border-gray-700/50 rounded-lg px-3 py-2">
                  <div className="text-xs text-gray-300">{rel.type}</div>
                  <div className="text-[10px] text-gray-500 mt-0.5 line-clamp-2">{rel.description}</div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
