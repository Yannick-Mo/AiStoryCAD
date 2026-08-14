import { useState, useEffect } from 'react'
import type { Chapter, Scene } from '../../types'
import { loadSceneContent } from '../../../../api/editor'
import { useToast } from '../../components/Toast'

interface ChapterDetailProps {
  chapter: Chapter | null
  onClose: () => void
  onSceneSave: (chapterId: string, sceneId: string, content: string) => void
  onChapterSave: (chapterId: string, goal: string) => void
  onOpenSceneEditor?: (scene: Scene) => void
  onUpdateChapter: (id: string, updates: Partial<Pick<Chapter, 'title' | 'status'>>) => void
  onUpdateScene: (chapterId: string, sceneId: string, updates: Partial<Pick<Scene, 'title' | 'povCharacter' | 'setting' | 'time' | 'summary'>>) => void
  onAddScene: (chapterId: string) => Scene
  onDeleteScene: (chapterId: string, sceneId: string) => void
  projectId?: string
  onOpenAiPanel?: (contextView: string, contextId?: string) => void
}

const STATUS_OPTIONS = [
  { value: 'draft' as const, label: '草稿' },
  { value: 'revising' as const, label: '修改' },
  { value: 'final' as const, label: '定稿' },
]

export default function ChapterDetail({ chapter, onClose, onSceneSave, onChapterSave, onOpenSceneEditor, onUpdateChapter, onUpdateScene, onAddScene, onDeleteScene, projectId, onOpenAiPanel }: ChapterDetailProps) {
  const [editSceneId, setEditSceneId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editGoal, setEditGoal] = useState('')
  const [saving, setSaving] = useState(false)
  const [contentCache, setContentCache] = useState<Record<string, string>>({})
  const { addToast } = useToast()

  useEffect(() => {
    if (!projectId || !chapter) return
    for (const sc of chapter.scenes) {
      if (sc.wordCount > 0 && !sc.content && !contentCache[sc.id]) {
        loadSceneContent(projectId, sc.id)
          .then(text => { if (text) setContentCache(prev => ({ ...prev, [sc.id]: text })) })
          .catch(() => {})
      }
    }
  }, [chapter?.id, projectId])

  useEffect(() => {
    setEditGoal(chapter?.goal ?? '')
  }, [chapter?.id])

  if (!chapter) return null

  const totalWords = chapter.scenes.reduce((s, sc) => s + sc.wordCount, 0)

  const sceneContent = (sc: Scene) => sc.content || contentCache[sc.id] || ''

  const startEdit = (scene: Scene) => {
    setEditSceneId(scene.id)
    setEditContent(sceneContent(scene))
  }

  const saveScene = async () => {
    if (!editSceneId) return
    setSaving(true)
    try {
      await onSceneSave(chapter.id, editSceneId, editContent)
      addToast('保存成功', 'success')
      setEditSceneId(null)
    } catch {
      addToast('保存失败，请重试', 'error')
    } finally {
      setSaving(false)
    }
  }

  const handleAddScene = () => {
    const sc = onAddScene(chapter.id)
    setEditSceneId(sc.id)
    setEditContent('')
  }

  return (
    <div className="h-full bg-gray-900/95 backdrop-blur-xl flex flex-col shadow-2xl">
      {/* Header */}
      <div className="p-4 border-b border-gray-800 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 space-y-2">
            <div className="flex items-center gap-2">
              <input
                value={chapter.title}
                onChange={e => onUpdateChapter(chapter.id, { title: e.target.value })}
                className="flex-1 bg-transparent font-medium text-amber-100 outline-none border-b border-transparent focus:border-amber-600/50"
              />
              <select
                value={chapter.status}
                onChange={e => onUpdateChapter(chapter.id, { status: e.target.value as Chapter['status'] })}
                className={`px-1.5 py-0.5 rounded text-[10px] outline-none border border-transparent focus:border-amber-600/50 ${
                  chapter.status === 'final' ? 'bg-green-900/30 text-green-400' :
                  chapter.status === 'revising' ? 'bg-amber-900/30 text-amber-400' :
                  'bg-gray-800 text-gray-400'
                }`}
              >
                {STATUS_OPTIONS.map(opt => (
                  <option key={opt.value} value={opt.value} className="bg-gray-900 text-gray-300">{opt.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-3 text-[10px] text-gray-500">
              <span>{chapter.scenes.length} 场</span>
              <span>{totalWords > 0 ? `${totalWords} 字` : '未开始'}</span>
            </div>
          </div>
          <button onClick={onClose} className="text-gray-500 hover:text-white text-lg leading-none shrink-0">✕</button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Goal section */}
        <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-1.5">📝 本章目标</div>
          <textarea
            value={editGoal}
            onChange={e => setEditGoal(e.target.value)}
            onBlur={() => { if (editGoal !== chapter.goal) onChapterSave(chapter.id, editGoal) }}
            placeholder="写一段话概括本章要完成什么..."
            className="w-full bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 leading-relaxed"
            rows={3}
          />
        </section>

        {/* Scenes section */}
        <section>
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] text-gray-500">🎬 场景 ({chapter.scenes.length})</span>
            <button
              onClick={handleAddScene}
              className="text-[10px] px-2 py-1 rounded bg-amber-600/20 text-amber-400 hover:bg-amber-600/30 transition-colors"
            >
              + 添加场景
            </button>
          </div>

          {chapter.scenes.length === 0 ? (
            <div className="text-center py-8 bg-gray-800/20 border border-dashed border-gray-700/50 rounded-xl">
              <div className="text-gray-600 text-xs mb-3">暂无场景，添加第一个场景开始创作</div>
              <button
                onClick={handleAddScene}
                className="px-4 py-2 rounded-lg bg-amber-600/20 text-amber-400 text-sm hover:bg-amber-600/30 transition-colors"
              >
                + 创建场景
              </button>
            </div>
          ) : (
            <div className="space-y-2">
              {chapter.scenes.map(scene => (
                <div key={scene.id} className="bg-gray-800/60 border border-gray-700/50 rounded-xl overflow-hidden">
                  <div className="p-3 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <input
                        value={scene.title}
                        onChange={e => onUpdateScene(chapter.id, scene.id, { title: e.target.value })}
                        className="flex-1 bg-transparent text-sm font-medium text-gray-200 outline-none border-b border-transparent focus:border-amber-600/50"
                      />
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-[10px] text-gray-500">{scene.wordCount > 0 ? `${scene.wordCount} 字` : '空'}</span>
                        <button
                          onClick={() => onDeleteScene(chapter.id, scene.id)}
                          className="text-gray-600 hover:text-red-400 transition-colors text-[10px]"
                          title="删除场景"
                        >✕</button>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-gray-500">
                      <span className="flex items-center gap-1">
                        🎭
                        <input
                          value={scene.povCharacter}
                          onChange={e => onUpdateScene(chapter.id, scene.id, { povCharacter: e.target.value })}
                          className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-16"
                          placeholder="POV"
                        />
                      </span>
                      <span className="flex items-center gap-1">
                        📍
                        <input
                          value={scene.setting}
                          onChange={e => onUpdateScene(chapter.id, scene.id, { setting: e.target.value })}
                          className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-20"
                          placeholder="场景"
                        />
                      </span>
                      <span className="flex items-center gap-1">
                        ⏰
                        <input
                          value={scene.time}
                          onChange={e => onUpdateScene(chapter.id, scene.id, { time: e.target.value })}
                          className="bg-transparent outline-none border-b border-transparent focus:border-amber-600/50 w-20"
                          placeholder="时间"
                        />
                      </span>
                    </div>
                    <textarea
                      value={scene.summary}
                      onChange={e => onUpdateScene(chapter.id, scene.id, { summary: e.target.value })}
                      placeholder="梗概..."
                      className="w-full bg-transparent text-[11px] text-gray-400 italic outline-none resize-none border-b border-transparent focus:border-amber-600/50 leading-relaxed"
                      rows={1}
                    />
                    {editSceneId === scene.id ? (
                      <div className="space-y-2">
                        <textarea
                          value={editContent}
                          onChange={e => setEditContent(e.target.value)}
                          className="w-full h-32 bg-gray-950 border border-gray-700 rounded-lg p-2 text-xs text-gray-300 resize-none focus:outline-none focus:border-amber-600 font-mono leading-relaxed"
                          placeholder="在这里写小说正文..."
                        />
                        <div className="flex gap-2">
                          <button onClick={saveScene} disabled={saving} className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${saving ? 'bg-gray-600 text-gray-400 cursor-not-allowed' : 'bg-amber-600 text-black hover:bg-amber-500'}`}>{saving ? '保存中...' : '保存'}</button>
                          <button onClick={() => setEditSceneId(null)} className="px-3 py-1 rounded-lg bg-gray-700 text-xs text-gray-300 hover:bg-gray-600 transition-colors">取消</button>
                        </div>
                      </div>
                    ) : (
                      <button
                        onClick={() => startEdit(scene)}
                        className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${
                          sceneContent(scene)
                            ? 'bg-gray-950/50 text-gray-400 hover:bg-gray-800 border border-gray-800'
                            : 'bg-gray-800/30 text-gray-600 hover:bg-gray-700 border border-dashed border-gray-700'
                        }`}
                      >
                        {sceneContent(scene)
                          ? <span className="line-clamp-3 font-mono leading-relaxed">{sceneContent(scene)}</span>
                          : '✏️ 点击开始写作...'}
                      </button>
                    )}
                    {editSceneId !== scene.id && (
                      <button
                        onClick={(e) => { e.stopPropagation(); onOpenSceneEditor?.(scene) }}
                        className="w-full px-2 py-1 rounded text-[10px] text-gray-600 hover:text-gray-400 hover:bg-gray-700/50 transition-colors text-center"
                      >
                        全屏编辑 ↗
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>

        {/* AI Assist section */}
        <section className="bg-gray-800/20 border border-gray-700/30 rounded-xl p-3">
          <div className="text-[10px] text-gray-500 mb-2">🤖 AI 辅助</div>
          <button
            onClick={() => onOpenAiPanel?.('chapter', chapter.id)}
            className="w-full text-left px-3 py-2 rounded-lg text-xs text-gray-400 hover:text-amber-300 hover:bg-gray-700/40 transition-colors border border-gray-700/30"
          >
            ✨ 打开 AI 助手
          </button>
        </section>
      </div>
    </div>
  )
}
