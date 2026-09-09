import { useState, useEffect } from 'react'
import type { Act, Chapter, Scene } from '../../types'
import { loadSceneContent } from '../../../../api/editor'
import { useToast } from '../../components/Toast'
import { useSessionState } from '../../../../hooks/useSessionState'

interface ActDetailProps {
  act: Act
  chapters: Chapter[]
  onSelectChapter: (chapterId: string) => void
  onSceneSave: (chapterId: string, sceneId: string, content: string) => void
  onOpenSceneEditor?: (scene: Scene) => void
  onUpdateAct: (id: string, updates: Partial<Pick<Act, 'name' | 'color'>>) => void
  onUpdateScene: (chapterId: string, sceneId: string, updates: Partial<Pick<Scene, 'title' | 'povCharacter' | 'setting' | 'time' | 'summary'>>) => void
  onAddChapter: (actId: string) => void
  onDeleteScene: (chapterId: string, sceneId: string) => void
  projectId?: string
}

const STATUS_OPTIONS = [
  { value: 'draft' as const, label: '草稿' },
  { value: 'revising' as const, label: '修改' },
  { value: 'final' as const, label: '定稿' },
]

export default function ActDetail({ act, chapters, onSelectChapter, onSceneSave, onOpenSceneEditor, onUpdateAct, onUpdateScene, onAddChapter, onDeleteScene, projectId }: ActDetailProps) {
  // session state: the panel remounts when it floats / docks / moves in the dock
  const [expandedId, setExpandedId] = useSessionState<string | null>('act.expandedId', null)
  const [editSceneId, setEditSceneId] = useSessionState<string | null>('act.editSceneId', null)
  const [editContent, setEditContent] = useSessionState('act.editContent', '')
  const [saving, setSaving] = useState(false)
  const [contentCache, setContentCache] = useSessionState<Record<string, string>>('act.contentCache', {})
  const { addToast } = useToast()

  const totalWords = chapters.reduce((s, c) => s + c.wordCount, 0)
  const totalScenes = chapters.reduce((s, c) => s + c.scenes.length, 0)

  useEffect(() => {
    if (!projectId || !expandedId) return
    const ch = chapters.find(c => c.id === expandedId)
    if (!ch) return
    for (const sc of ch.scenes) {
      if (sc.wordCount > 0 && !sc.content && !contentCache[sc.id]) {
        loadSceneContent(projectId, sc.id)
          .then(text => { if (text) setContentCache(prev => ({ ...prev, [sc.id]: text })) })
          .catch(() => {})
      }
    }
  }, [expandedId, projectId, chapters])

  const toggleExpand = (chId: string) => {
    setExpandedId(expandedId === chId ? null : chId)
    setEditSceneId(null)
  }

  const sceneContent = (sc: Scene) => sc.content || contentCache[sc.id] || ''

  const startEdit = (scene: Scene) => {
    setEditSceneId(scene.id)
    setEditContent(sceneContent(scene))
  }

  const saveScene = async (chapterId: string) => {
    if (!editSceneId) return
    setSaving(true)
    try {
      await onSceneSave(chapterId, editSceneId, editContent)
      addToast('保存成功', 'success')
      setEditSceneId(null)
    } catch {
      addToast('保存失败，请重试', 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="p-4 border-b border-gray-800 space-y-2" style={{ borderLeft: `3px solid ${act.color}` }}>
        <div className="flex items-center justify-between gap-2">
          <input
            value={act.name}
            onChange={e => onUpdateAct(act.id, { name: e.target.value })}
            className="font-medium text-amber-100 bg-transparent border-b border-transparent focus:border-amber-600/50 outline-none flex-1"
          />
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={act.color}
              onChange={e => onUpdateAct(act.id, { color: e.target.value })}
              className="w-16 bg-transparent text-[10px] text-gray-400 border-b border-transparent focus:border-amber-600/50 outline-none font-mono text-center"
            />
            <span className="w-4 h-4 rounded-full border border-gray-700 shrink-0" style={{ backgroundColor: act.color }} />
          </div>
        </div>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3 text-[10px] text-gray-500">
            <span>{chapters.length} 章</span>
            <span>{totalScenes} 场</span>
            <span>{totalWords > 0 ? `${totalWords} 字` : '未开始'}</span>
            <span>{chapters.filter(c => c.status === 'final').length}/{chapters.length} 完成</span>
          </div>
          <button
            onClick={() => onAddChapter(act.id)}
            className="text-[10px] px-2 py-1 rounded bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 transition-colors"
          >
            + 添加章节
          </button>
        </div>
      </div>

      {/* Chapter list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-1.5">
        {chapters.length === 0 && (
          <div className="text-center py-12">
            <div className="text-gray-600 text-sm mb-3">该幕暂无章节</div>
            <button
              onClick={() => onAddChapter(act.id)}
              className="px-4 py-2 rounded-lg bg-amber-600/20 text-amber-400 text-sm hover:bg-amber-600/30 transition-colors"
            >
              + 创建首个章节
            </button>
          </div>
        )}
        {chapters.map(ch => {
          const isExpanded = expandedId === ch.id
          return (
            <div key={ch.id} className="bg-gray-800/40 border border-gray-700/40 rounded-xl overflow-hidden">
              <button
                onClick={() => toggleExpand(ch.id)}
                className="w-full flex items-center gap-2 px-3 py-2.5 hover:bg-gray-700/30 transition-colors text-left"
              >
                <span className="text-xs text-gray-500 w-4 shrink-0">{isExpanded ? '▾' : '▸'}</span>
                <span className="text-sm font-medium text-gray-200 flex-1 truncate">{ch.title}</span>
                <span className={`px-1.5 py-0.5 rounded text-[10px] ${
                  ch.status === 'final' ? 'bg-green-900/30 text-green-400' :
                  ch.status === 'revising' ? 'bg-amber-900/30 text-amber-400' :
                  'bg-gray-800 text-gray-500'
                }`}>{STATUS_OPTIONS.find(s => s.value === ch.status)?.label}</span>
                <span className="text-[10px] text-gray-600 w-10 text-right">{ch.wordCount > 0 ? `${ch.wordCount}w` : '-'}</span>
                <button
                  onClick={(e) => { e.stopPropagation(); onSelectChapter(ch.id) }}
                  className="text-[10px] text-gray-600 hover:text-amber-400 transition-colors px-1"
                  title="聚焦到本章"
                >🔍</button>
              </button>

              {isExpanded && (
                <div className="px-3 pb-3 space-y-2 border-t border-gray-700/30 pt-2">
                  {ch.scenes.map(scene => (
                    <div key={scene.id} className="bg-gray-800/60 border border-gray-700/40 rounded-lg p-2.5">
                      <div className="flex items-center gap-1">
                        <input
                          value={scene.title}
                          onChange={e => onUpdateScene(ch.id, scene.id, { title: e.target.value })}
                          className="bg-transparent text-xs font-medium text-gray-300 outline-none border-b border-transparent focus:border-amber-600/50 flex-1"
                        />
                        <button
                          onClick={() => onDeleteScene(ch.id, scene.id)}
                          className="text-gray-600 hover:text-red-400 transition-colors text-[10px] px-1 shrink-0"
                          title="删除场景"
                        >✕</button>
                      </div>
                      <div className="flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-gray-600 mb-1.5">
                        <span className="flex items-center gap-1">
                          🎭
                          <input
                            value={scene.povCharacter}
                            onChange={e => onUpdateScene(ch.id, scene.id, { povCharacter: e.target.value })}
                            className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-16"
                            placeholder="POV"
                          />
                        </span>
                        <span className="flex items-center gap-1">
                          📍
                          <input
                            value={scene.setting}
                            onChange={e => onUpdateScene(ch.id, scene.id, { setting: e.target.value })}
                            className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-20"
                            placeholder="场景"
                          />
                        </span>
                        <span className="flex items-center gap-1">
                          ⏰
                          <input
                            value={scene.time}
                            onChange={e => onUpdateScene(ch.id, scene.id, { time: e.target.value })}
                            className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-20"
                            placeholder="时间"
                          />
                        </span>
                      </div>
                      <textarea
                        value={scene.summary}
                        onChange={e => onUpdateScene(ch.id, scene.id, { summary: e.target.value })}
                        placeholder="梗概..."
                        className="w-full bg-transparent text-[11px] text-gray-400 italic outline-none resize-none border-b border-transparent focus:border-amber-600/50 mb-1.5 leading-relaxed"
                        rows={1}
                      />
                      {editSceneId === scene.id ? (
                        <div className="space-y-1.5">
                          <textarea
                            value={editContent}
                            onChange={e => setEditContent(e.target.value)}
                            className="w-full h-28 bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 font-mono leading-relaxed"
                            placeholder="写小说正文..."
                          />
                          <div className="flex gap-2">
                            <button onClick={() => saveScene(ch.id)} disabled={saving} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${saving ? 'bg-gray-600 text-gray-400 cursor-not-allowed' : 'bg-amber-600 text-black hover:bg-amber-500'}`}>{saving ? '保存中...' : '保存'}</button>
                            <button onClick={() => setEditSceneId(null)} className="px-3 py-1 rounded-lg bg-gray-700 text-xs text-gray-300 hover:bg-gray-600 transition-colors">取消</button>
                          </div>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEdit(scene)}
                          className={`w-full text-left px-2.5 py-2 rounded-lg text-xs transition-colors ${
                            sceneContent(scene)
                              ? 'bg-gray-950/50 text-gray-400 hover:bg-gray-800 border border-gray-800'
                              : 'bg-gray-800/30 text-gray-600 hover:bg-gray-700 border border-dashed border-gray-700'
                          }`}
                        >
                          {sceneContent(scene)
                            ? <span className="line-clamp-2 font-mono leading-relaxed">{sceneContent(scene)}</span>
                            : '✏️ 点击开始写作...'}
                        </button>
                      )}
                      {editSceneId !== scene.id && (
                        <button
                          onClick={(e) => { e.stopPropagation(); onOpenSceneEditor?.(scene) }}
                          className="w-full mt-1 px-2 py-1 rounded text-[10px] text-gray-600 hover:text-gray-400 hover:bg-gray-700/50 transition-colors text-center"
                        >
                          全屏编辑 ↗
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
