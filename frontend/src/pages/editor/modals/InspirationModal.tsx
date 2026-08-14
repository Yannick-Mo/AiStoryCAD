import { useState } from 'react'
import {
  generateStarter,
  batchGenerate,
  getChallenges,
  getRandomChallenge,
} from '../../../api/inspiration'
import type { StoryStarter, CreativeChallenge } from '../../../api/inspiration'

const GENRES = ['网络爽文', '悬疑', '言情', '现实主义', '奇幻', '科幻', '历史', '恐怖']

const DIFFICULTY_MAP: Record<string, string> = {
  '全部': '',
  '简单': 'easy',
  '中等': 'medium',
  '困难': 'hard',
}

interface Props {
  onClose: () => void
  onApplyStarter?: (title: string) => void
}

export default function InspirationModal({ onClose, onApplyStarter }: Props) {
  const [tab, setTab] = useState<'starter' | 'challenge'>('starter')

  return (
    <div className="fixed inset-0 bg-gray-950/80 backdrop-blur-sm z-50 flex items-center justify-center">
      <div className="w-full max-w-2xl max-h-[85vh] overflow-y-auto bg-gray-900 border border-gray-800 rounded-2xl shadow-2xl mx-4">
        <div className="flex items-center justify-between px-5 h-12 border-b border-gray-800">
          <div className="flex gap-4">
            <button
              onClick={() => setTab('starter')}
              className={`text-sm font-medium transition-colors ${
                tab === 'starter' ? 'text-amber-400' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              故事起点
            </button>
            <button
              onClick={() => setTab('challenge')}
              className={`text-sm font-medium transition-colors ${
                tab === 'challenge' ? 'text-amber-400' : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              创作挑战
            </button>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg">✕</button>
        </div>

        <div className="p-5">
          {tab === 'starter' ? <StarterTab onApplyStarter={onApplyStarter} /> : <ChallengeTab />}
        </div>
      </div>
    </div>
  )
}

function StarterTab({ onApplyStarter }: { onApplyStarter?: (title: string) => void }) {
  const [genre, setGenre] = useState('网络爽文')
  const [style, setStyle] = useState('')
  const [loading, setLoading] = useState(false)
  const [batchLoading, setBatchLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<StoryStarter | null>(null)
  const [batchResults, setBatchResults] = useState<StoryStarter[]>([])

  const handleGenerate = async () => {
    setLoading(true)
    setError(null)
    setResult(null)
    setBatchResults([])
    try {
      const data = await generateStarter(genre, style || undefined)
      setResult(data)
    } catch (e: any) {
      setError(e.message || '生成失败')
    } finally {
      setLoading(false)
    }
  }

  const handleBatch = async () => {
    setBatchLoading(true)
    setError(null)
    setBatchResults([])
    setResult(null)
    try {
      const data = await batchGenerate([genre], 3)
      setBatchResults(data)
    } catch (e: any) {
      setError(e.message || '批量生成失败')
    } finally {
      setBatchLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <div className="flex-1">
          <label className="text-[10px] text-gray-500 mb-1 block">类型</label>
          <select
            value={genre}
            onChange={e => setGenre(e.target.value)}
            disabled={loading || batchLoading}
            className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-300 focus:outline-none focus:border-amber-600 disabled:opacity-50"
          >
            {GENRES.map(g => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
        </div>
        <div className="flex-[2]">
          <label className="text-[10px] text-gray-500 mb-1 block">风格（可选）</label>
          <input
            value={style}
            onChange={e => setStyle(e.target.value)}
            placeholder="如：轻松、黑暗、悬疑..."
            disabled={loading || batchLoading}
            className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-300 placeholder-gray-600 focus:outline-none focus:border-amber-600 disabled:opacity-50"
          />
        </div>
      </div>

      <div className="flex gap-2">
        <button
          onClick={handleGenerate}
          disabled={loading || batchLoading}
          className={`flex-1 px-4 py-2 rounded-xl text-xs font-medium text-black transition-colors ${
            loading ? 'bg-amber-800 animate-pulse' : 'bg-amber-600 hover:bg-amber-500'
          } disabled:opacity-50`}
        >
          {loading ? '生成中...' : '生成'}
        </button>
        <button
          onClick={handleBatch}
          disabled={loading || batchLoading}
          className={`px-4 py-2 rounded-xl text-xs font-medium transition-colors ${
            batchLoading ? 'bg-amber-800 text-amber-200 animate-pulse' : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
          } disabled:opacity-50`}
        >
          {batchLoading ? '生成中...' : '批量生成'}
        </button>
      </div>

      {error && (
        <div className="bg-red-900/20 border border-red-800/30 rounded-lg p-3">
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {result && <StarterCard starter={result} onApply={onApplyStarter} />}

      {batchResults.length > 0 && (
        <div className="grid grid-cols-1 gap-3">
          {batchResults.map((s, i) => (
            <StarterCard key={i} starter={s} index={i + 1} onApply={onApplyStarter} />
          ))}
        </div>
      )}
    </div>
  )
}

function StarterCard({ starter, index, onApply }: { starter: StoryStarter; index?: number; onApply?: (title: string) => void }) {
  const [applied, setApplied] = useState(false)

  return (
    <div className="bg-gray-950/60 border border-gray-800 rounded-xl p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          {index && <span className="text-[10px] text-gray-500 mr-2">#{index}</span>}
          <h4 className="text-sm font-medium text-amber-100">{starter.title}</h4>
        </div>
        {onApply && (
          <button
            onClick={() => { onApply(starter.title); setApplied(true) }}
            disabled={applied}
            className={`shrink-0 px-2.5 py-1 rounded-lg text-[10px] font-medium transition-colors ${
              applied
                ? 'bg-green-900/40 text-green-400'
                : 'bg-amber-600/20 text-amber-400 hover:bg-amber-600/30'
            }`}
          >
            {applied ? '已应用' : '应用标题'}
          </button>
        )}
      </div>

      <div className="bg-amber-600/10 border border-amber-600/20 rounded-lg px-3 py-2">
        <p className="text-xs text-amber-300 leading-relaxed">{starter.hook}</p>
      </div>

      <div>
        <span className="text-[10px] text-amber-500/80">核心设定</span>
        <p className="text-xs text-gray-400 leading-relaxed mt-1">{starter.premise}</p>
      </div>

      <div>
        <span className="text-[10px] text-amber-500/80">主角原型</span>
        <p className="text-xs text-gray-400 leading-relaxed mt-1">{starter.protagonist}</p>
      </div>

      <div>
        <span className="text-[10px] text-amber-500/80">开篇场景</span>
        <p className="text-xs text-gray-300 leading-relaxed mt-1 whitespace-pre-wrap">{starter.opening_scene}</p>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {starter.themes?.map((t, i) => (
          <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">{t}</span>
        ))}
        {starter.tags?.map((t, i) => (
          <span key={i} className="text-[10px] px-2 py-0.5 rounded-full bg-amber-900/30 text-amber-400">{t}</span>
        ))}
      </div>
    </div>
  )
}

function ChallengeTab() {
  const [difficultyFilter, setDifficultyFilter] = useState('')
  const [, setError] = useState<string | null>(null)
  const [genreFilter, setGenreFilter] = useState('')
  const [challenges, setChallenges] = useState<CreativeChallenge[]>([])
  const [randomChallenge, setRandomChallenge] = useState<CreativeChallenge | null>(null)
  const [loading, setLoading] = useState(false)

  const handleFilter = async () => {
    setLoading(true)
    try {
      const data = await getChallenges(difficultyFilter || undefined, genreFilter || undefined)
      setChallenges(data)
    } catch (err) {
      const message = err instanceof Error ? err.message : '获取失败';
      setError(message);
    } finally {
      setLoading(false)
    }
  }

  const handleRandom = async () => {
    setRandomChallenge(null)
    try {
      const data = await getRandomChallenge()
      setRandomChallenge(data)
    } catch (err) {
      const message = err instanceof Error ? err.message : '获取失败';
      setError(message);
    }
  }

  const diffButtons = ['全部', '简单', '中等', '困难']

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        {diffButtons.map(label => (
          <button
            key={label}
            onClick={() => setDifficultyFilter(DIFFICULTY_MAP[label])}
            className={`px-3 py-1 rounded-lg text-[11px] font-medium transition-colors ${
              difficultyFilter === DIFFICULTY_MAP[label]
                ? 'bg-amber-600 text-black'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            {label}
          </button>
        ))}
        <select
          value={genreFilter}
          onChange={e => setGenreFilter(e.target.value)}
          className="ml-2 bg-gray-950 border border-gray-700 rounded-lg px-2 py-1 text-[11px] text-gray-300 focus:outline-none focus:border-amber-600"
        >
          <option value="">全部类型</option>
          {GENRES.map(g => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
        <button
          onClick={handleFilter}
          disabled={loading}
          className="px-3 py-1 rounded-lg text-[11px] font-medium bg-amber-600 text-black hover:bg-amber-500 transition-colors disabled:opacity-50"
        >
          筛选
        </button>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-[10px] text-gray-500">
            {challenges.length > 0 ? `${challenges.length} 个挑战` : ''}
          </span>
          <button
            onClick={handleRandom}
            className="px-3 py-1 rounded-lg text-[11px] font-medium bg-gray-800 text-gray-400 hover:bg-gray-700 transition-colors"
          >
            换一个
          </button>
        </div>

        {randomChallenge && !challenges.length && (
          <ChallengeCard challenge={randomChallenge} />
        )}

        {challenges.length > 0 && (
          <div className="grid grid-cols-1 gap-3">
            {challenges.map((c, i) => (
              <ChallengeCard key={i} challenge={c} />
            ))}
          </div>
        )}

        {!challenges.length && !randomChallenge && (
          <div className="text-center text-gray-600 text-xs py-8">
            点击筛选查看挑战，或点"换一个"随机获取
          </div>
        )}
      </div>
    </div>
  )
}

function ChallengeCard({ challenge }: { challenge: CreativeChallenge }) {
  const difficultyLabel =
    challenge.difficulty === 'easy' ? '简单' :
    challenge.difficulty === 'hard' ? '困难' : '中等'

  const difficultyColor =
    challenge.difficulty === 'easy' ? 'text-green-400' :
    challenge.difficulty === 'hard' ? 'text-red-400' : 'text-yellow-400'

  return (
    <div className="bg-gray-950/60 border border-gray-800 rounded-xl p-4 space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-amber-100">{challenge.title}</h4>
        <div className="flex gap-2">
          <span className={`text-[10px] px-2 py-0.5 rounded-full bg-gray-800 text-gray-400`}>
            {challenge.genre}
          </span>
          <span className={`text-[10px] px-2 py-0.5 rounded-full bg-gray-800 ${difficultyColor}`}>
            {difficultyLabel}
          </span>
        </div>
      </div>
      <p className="text-xs text-gray-400 leading-relaxed">{challenge.description}</p>
      <div className="space-y-1">
        {challenge.constraints.map((c, i) => (
          <div key={i} className="flex items-start gap-2 text-[11px] text-gray-500">
            <span className="text-amber-500/60 mt-0.5">•</span>
            <span>{c}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
