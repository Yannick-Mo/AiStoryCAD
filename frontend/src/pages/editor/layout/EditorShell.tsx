import { useState, useCallback, useEffect, useRef } from 'react'
import BottomNav from './BottomNav'
import LeftDrawer from './LeftDrawer'
import ActionButtons from './ActionButtons'
import PlotCanvas from '../views/plot/PlotCanvas'
import PlotToolbar from '../views/plot/PlotToolbar'
import ChapterDetail from '../views/plot/ChapterDetail'
import ActDetail from '../views/plot/ActDetail'
import EdgeDetail from '../views/plot/EdgeDetail'
import CharCanvas from '../views/character/CharCanvas'
import CharacterDetail from '../views/character/CharacterDetail'
import CharacterEdgeDetail from '../views/character/CharacterEdgeDetail'
import RhythmCanvas from '../views/rhythm/RhythmCanvas'
import RhythmDetail from '../views/rhythm/RhythmDetail'
import ThemeCanvas from '../views/theme/ThemeCanvas'
import ThemeDetail from '../views/theme/ThemeDetail'
import PreviewModal from '../modals/PreviewModal'
import SceneEditor from '../modals/SceneEditor'
import GlobalSettingsModal from '../modals/GlobalSettingsModal'
import AiPanel from '../modals/AiChatPanel'
import InspirationModal from '../modals/InspirationModal'
import ConsistencyCheckModal from '../modals/ConsistencyCheckModal'
import { useEditorViews } from '../hooks/useEditorViews'
import { useEditorStore } from '../data/editorStore'
import { loadEditorData, saveSceneContent } from '../../../api/editor'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import ResizablePanel from '../components/ResizablePanel'
import type { Chapter, Scene, EdgeType } from '../types'
import { getCompletedChain } from '../data/orderUtils'

export default function EditorShell({ projectId }: { projectId: string }) {
  const views = useEditorViews()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [globalSettingsOpen, setGlobalSettingsOpen] = useState(false)
  const [selectedActId, setSelectedActId] = useState<string | null>(null)
  const [selectedChapter, setSelectedChapter] = useState<Chapter | null>(null)
  const [connectionMode, setConnectionMode] = useState<'all' | EdgeType>('all')
  const [editingScene, setEditingScene] = useState<Scene | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<{ type: 'act' | 'chapter' | 'scene'; id: string; chapterId?: string } | null>(null)
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null)
  const [selectedRelation, setSelectedRelation] = useState<{ sourceId: string; relationId: string } | null>(null)
  const [selectedRhythmIndex, setSelectedRhythmIndex] = useState<number | null>(null)
  const [selectedTheme, setSelectedTheme] = useState<{ themeIndex: number; chapterIndex: number } | null>(null)
  const [showAddTheme, setShowAddTheme] = useState(false)
  const [deleteThemeId, setDeleteThemeId] = useState<string | null>(null)
  const [newThemeName, setNewThemeName] = useState('')
  const [newThemeColor, setNewThemeColor] = useState('#d4a373')
  const [newThemeProposition, setNewThemeProposition] = useState('')
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [aiContextView, setAiContextView] = useState<string>('chat')
  const [aiContextId, setAiContextId] = useState<string | undefined>(undefined)
  const [inspirationOpen, setInspirationOpen] = useState(false)
  const [consistencyOpen, setConsistencyOpen] = useState(false)
  const [triggerRhythmAnalysis, setTriggerRhythmAnalysis] = useState(false)

  const { addToast } = useToast()

  const store = useEditorStore(projectId, useCallback((msg: string) => {
    addToast(msg, 'error')
  }, [addToast]))
  const data = store.data
  const [layoutKey, setLayoutKey] = useState(0)
  const handleAutoLayout = useCallback(() => setLayoutKey(k => k + 1), [])

  const handleRhythmAnalysis = () => {
    views.switchView('narrative-rhythm')
    setTriggerRhythmAnalysis(true)
  }

  const handleActClick = useCallback((actId: string) => {
    if (!actId) { setSelectedActId(null); store.clearSelection(); return }
    setSelectedActId(actId); setSelectedChapter(null); store.selectNode('act', actId)
  }, [store])

  const handleChapterClick = useCallback((chapterId: string) => {
    if (!data) return
    const ch = data.chapters.find(c => c.id === chapterId)
    if (!ch) return
    setSelectedChapter(ch); setSelectedActId(null); store.selectNode('chapter', chapterId)
  }, [data, store])

  const setData = useCallback(store.setData, [])

  const handleSceneSaved = useCallback((sceneId: string, content: string, wordCount: number) => {
    let updatedChapter: Chapter | undefined
    setData(d => {
      if (!d) return d
      const newChapters = d.chapters.map(ch => {
        if (!ch.scenes.some(s => s.id === sceneId)) return ch
        const newScenes = ch.scenes.map(s => s.id === sceneId ? { ...s, content, wordCount } : s)
        const updated = { ...ch, scenes: newScenes, wordCount: newScenes.reduce((sum, sc) => sum + sc.wordCount, 0) }
        updatedChapter = updated
        return updated
      })
      return { ...d, chapters: newChapters }
    })
    if (updatedChapter) setSelectedChapter(updatedChapter)
    setEditingScene(null)
  }, [setData])

  const handleChapterGoalSave = useCallback((chapterId: string, goal: string) => {
    setData(d => d ? { ...d, chapters: d.chapters.map(c => c.id === chapterId ? { ...c, goal } : c) } : d)
    if (selectedChapter?.id === chapterId) setSelectedChapter({ ...selectedChapter, goal })
  }, [setData, selectedChapter])

  const handleOpenAiPanel = useCallback((contextView: string, contextId?: string) => {
    setAiContextView(contextView)
    setAiContextId(contextId)
    setAiChatOpen(true)
  }, [])

  const handleAiChatOpen = useCallback(() => {
    setAiContextView('chat')
    setAiContextId(undefined)
    setAiChatOpen(true)
  }, [])

  const handleProjectUpdated = useCallback(() => {
    store.flushChanges().then(ok => {
      if (ok) {
        loadEditorData(projectId).then(d => store.setData(d)).catch((err) => {
          console.error('Failed to reload project data:', err)
          addToast('重载项目数据失败', 'error')
        })
      } else {
        addToast('本地修改尚未保存，无法安全重载 AI 写入数据，请稍后再试', 'warning')
      }
    })
  }, [projectId, store, addToast])

  const hasPendingRef = useRef(store.hasPendingChanges)
  hasPendingRef.current = store.hasPendingChanges

  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (hasPendingRef.current()) {
        e.preventDefault()
        e.returnValue = ''
      }
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [])

  if (store.loading) return <div className="h-screen bg-gray-950 flex items-center justify-center text-gray-500 text-sm">加载项目数据...</div>
  if (store.error) return <div className="h-screen bg-gray-950 flex items-center justify-center text-red-400 text-sm">{store.error}</div>
  if (!data) return <div className="h-screen bg-gray-950 flex items-center justify-center text-gray-500 text-sm">暂无数据</div>

  const selectedEdge = store.selection.type === 'edge'
    ? data.edges.find(edge => edge.id === store.selection.id) ?? null
    : null

  const selectedAct = selectedActId ? data.acts.find(a => a.id === selectedActId) ?? null : null
  const activeChapter = selectedChapter ? data.chapters.find(c => c.id === selectedChapter.id) ?? null : null
  const selectedChar = selectedCharacterId ? data.characters.find(c => c.id === selectedCharacterId) ?? null : null

  const renderCanvas = () => {
    switch (views.activeViewId) {
      case 'narrative-plot':
        return (
          <PlotCanvas
            chapters={data.chapters}
            acts={data.acts}
            edges={data.edges}
            onChapterClick={handleChapterClick}
            onActClick={handleActClick}
            onAddEdge={store.addEdge}
            onDeleteEdge={store.deleteEdge}
            onChangeEdgeType={store.changeEdgeType}
            onReconnectEdge={store.reconnectEdge}
            onAddChapter={store.addChapter}
            onDeleteChapter={(id) => setConfirmDelete({ type: 'chapter', id })}
            onAddAct={store.addAct}
            onDeleteAct={(id) => setConfirmDelete({ type: 'act', id })}
            onActResize={store.resizeAct}
            selection={store.selection}
            onSelectNode={store.selectNode}
            onSelectEdge={(edgeId: string) => {
              setSelectedActId(null); setSelectedChapter(null); store.selectEdge(edgeId)
            }}
            onClearSelection={store.clearSelection}
            connectionMode={connectionMode}
            resetKey={layoutKey}
          />
        )
      case 'narrative-char':
        return (
          <CharCanvas
            characters={data.characters}
            selection={{ type: selectedCharacterId ? 'character' : selectedRelation ? 'relation' : null, id: selectedCharacterId ?? (selectedRelation ? `${selectedRelation.sourceId}|${selectedRelation.relationId}` : null) }}
            onSelectCharacter={(id) => { setSelectedCharacterId(id); setSelectedRelation(null) }}
            onSelectRelation={(sourceId, relationId) => { setSelectedRelation({ sourceId, relationId }); setSelectedCharacterId(null) }}
            onClearSelection={() => { setSelectedCharacterId(null); setSelectedRelation(null) }}
            onAddCharacter={() => { const ch = store.addCharacter(); setSelectedCharacterId(ch.id) }}
            onDeleteCharacter={(id) => { store.deleteCharacter(id); setSelectedCharacterId(null) }}
            onAddRelation={(sourceId, targetId) => { store.addRelation(sourceId, targetId) }}
            onDeleteRelation={(characterId, relationId) => { store.deleteRelation(characterId, relationId); setSelectedRelation(null) }}
          />
        )
      case 'narrative-rhythm':
        return <RhythmCanvas projectId={projectId} rhythms={data.rhythms} chapters={data.chapters} acts={data.acts} selectedIndex={selectedRhythmIndex} onSelectChapter={setSelectedRhythmIndex} onSaveRhythm={(chapterId, values) => {
                const existing = data.rhythms.find(r => r.chapterId === chapterId)
                const rhythmId = existing?.id ?? crypto.randomUUID()
                store.setData(d => d ? {
                  ...d,
                  rhythms: d.rhythms.some(r => r.chapterId === chapterId)
                    ? d.rhythms.map(r => r.chapterId === chapterId ? { ...r, ...values } : r)
                    : (() => {
                        const idx = d.chapters.findIndex(c => c.id === chapterId)
                        return [...d.rhythms, { id: rhythmId, chapterId, chapterIndex: Math.max(idx, 0), label: idx >= 0 ? `第${idx + 1}章` : '?', ...values }]
                      })()
                } : d)
                if (existing) {
                  store.enqueueChange({ entity: 'rhythms', op: 'update', id: existing.id, data: values as Record<string, unknown> })
                } else {
                  store.enqueueChange({ entity: 'rhythms', op: 'create', data: { id: rhythmId, project_id: projectId, chapter_id: chapterId, ...values } })
                }
              }} autoAnalyze={triggerRhythmAnalysis} onAnalysisDone={() => setTriggerRhythmAnalysis(false)} />
      case 'narrative-theme':
        return <ThemeCanvas themes={data.themes} chapters={data.chapters} selected={selectedTheme} onSelect={(tIdx, chIdx) => setSelectedTheme({ themeIndex: tIdx, chapterIndex: chIdx })} onAddTheme={() => setShowAddTheme(true)} onDeleteTheme={(id) => setDeleteThemeId(id)} />
      default:
        return <div className="flex items-center justify-center h-full text-gray-500">选择视图</div>
    }
  }

  const handleExport = () => {
    const completed = getCompletedChain(data.chapters, data.edges, data.acts)
    const allChapters = completed.flat()
    let lastActId = ''
    const parts: string[] = []
    for (const ch of allChapters) {
      const act = data.acts.find(a => a.id === ch.actId)
      if (act && act.id !== lastActId) {
        parts.push(`\n${'='.repeat(40)}\n${act.name}\n${'='.repeat(40)}\n`)
        lastActId = act.id
      }
      parts.push(`\n## ${ch.title}\n`)
      if (ch.goal) parts.push(`目标：${ch.goal}\n\n`)
      for (const scene of ch.scenes) {
        if (scene.content) {
          if (scene.title) parts.push(`【${scene.title}】\n\n`)
          parts.push(scene.content + '\n\n')
        }
      }
    }
    const text = parts.join('')
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const title = data.projectTitle || '已完成内容'
    a.download = `${title}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  return (
      <div className="h-screen flex flex-col bg-gray-950 text-gray-100 overflow-hidden select-none">
        {/* Top bar with save indicator */}
        <div className="h-12 flex items-center justify-between px-4 border-b border-gray-800 bg-gray-900/50 shrink-0">
          <button
            onClick={() => setDrawerOpen(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs text-gray-400 hover:text-gray-200 bg-gray-800/50 hover:bg-gray-700 transition-colors"
          >
            ☰ 大纲
          </button>
          <div className="flex items-center gap-3">
            <button
              onClick={() => store.flushChanges()}
              disabled={!store.dirty || store.saving}
              className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                store.dirty
                  ? 'bg-amber-600 text-black hover:bg-amber-500'
                  : 'bg-gray-800 text-gray-600 cursor-default'
              }`}
            >
              {store.saving ? '保存中…' : store.dirty ? '保存' : '已保存'}
            </button>
            <div className="text-xs text-gray-500 bg-gray-800/50 px-3 py-1 rounded-full">
              {views.activeView.label}
            </div>
          </div>
        </div>

        {/* Canvas area */}
        <div className="flex-1 relative">
          {renderCanvas()}

          {views.activeViewId === 'narrative-plot' && (
            <PlotToolbar
              selection={store.selection}
              selectedActId={selectedActId}
              connectionMode={connectionMode}
              onConnectionModeChange={setConnectionMode}
              onAddAct={() => store.addAct()}
              onAddChapter={() => selectedActId && store.addChapter(selectedActId)}
              onDeleteSelected={() => {
                const sel = store.selection
                if (sel.type === 'act') setConfirmDelete({ type: 'act', id: sel.id! })
                if (sel.type === 'chapter') setConfirmDelete({ type: 'chapter', id: sel.id! })
                if (sel.type === 'edge') store.deleteEdge(sel.id!)
                store.clearSelection()
              }}
              onLayout={handleAutoLayout}
            />
          )}

          {/* Detail panels - Plot */}
          {views.activeViewId === 'narrative-plot' && (
            selectedAct ? (
              <ResizablePanel>
                <ActDetail
                  act={selectedAct}
                  chapters={data.chapters.filter(c => c.actId === selectedActId)}
                  onClose={() => setSelectedActId(null)}
                  onSelectChapter={(chId) => { setSelectedActId(null); setSelectedChapter(data.chapters.find(c => c.id === chId) ?? null) }}
                  projectId={projectId}
                  onSceneSave={async (chapterId, sceneId, content) => {
                    let updatedChapter: Chapter | undefined
                    setData(d => {
                      if (!d) return d
                      const chs = d.chapters.map(ch =>
                        ch.id === chapterId
                          ? { ...ch, scenes: ch.scenes.map(s => s.id === sceneId ? { ...s, content } : s) }
                          : ch
                      )
                      updatedChapter = chs.find(c => c.id === chapterId)
                      return { ...d, chapters: chs }
                    })
                    try {
                      const result = await saveSceneContent(projectId, sceneId, content)
                      setData(d => {
                        if (!d) return d
                        const chs = d.chapters.map(ch => {
                          if (ch.id !== chapterId) return ch
                          const newScenes = ch.scenes.map(s => s.id === sceneId ? { ...s, content, wordCount: result.word_count } : s)
                          return { ...ch, scenes: newScenes, wordCount: newScenes.reduce((sum, sc) => sum + sc.wordCount, 0) }
                        })
                        updatedChapter = chs.find(c => c.id === chapterId)
                        return { ...d, chapters: chs }
                      })
                      if (updatedChapter) setSelectedChapter({ ...updatedChapter })
                    } catch (e) {
                      throw e
                    }
                  }}
                  onOpenSceneEditor={(scene) => setEditingScene(scene)}
                  onUpdateAct={store.updateAct}
                  onUpdateScene={store.updateScene}
                  onAddChapter={store.addChapter}
                  onDeleteScene={(chapterId, sceneId) => setConfirmDelete({ type: 'scene', id: sceneId, chapterId })}
                />
              </ResizablePanel>
            ) : activeChapter ? (
              <ResizablePanel>
                <ChapterDetail
                  chapter={activeChapter}
                  projectId={projectId}
                  onClose={() => setSelectedChapter(null)}
                  onSceneSave={async (chapterId, sceneId, content) => {
                    let updatedChapter: Chapter | undefined
                    setData(d => {
                      if (!d) return d
                      const chs = d.chapters.map(ch =>
                        ch.id === chapterId
                          ? { ...ch, scenes: ch.scenes.map(s => s.id === sceneId ? { ...s, content } : s) }
                          : ch
                      )
                      updatedChapter = chs.find(c => c.id === chapterId)
                      return { ...d, chapters: chs }
                    })
                    try {
                      const result = await saveSceneContent(projectId, sceneId, content)
                      setData(d => {
                        if (!d) return d
                        const chs = d.chapters.map(ch => {
                          if (ch.id !== chapterId) return ch
                          const newScenes = ch.scenes.map(s => s.id === sceneId ? { ...s, content, wordCount: result.word_count } : s)
                          return { ...ch, scenes: newScenes, wordCount: newScenes.reduce((sum, sc) => sum + sc.wordCount, 0) }
                        })
                        updatedChapter = chs.find(c => c.id === chapterId)
                        return { ...d, chapters: chs }
                      })
                      if (updatedChapter) setSelectedChapter({ ...updatedChapter })
                    } catch (e) {
                      throw e
                    }
                  }}
                  onChapterSave={handleChapterGoalSave}
                  onOpenSceneEditor={(scene) => setEditingScene(scene)}
                  onUpdateChapter={store.updateChapter}
                  onUpdateScene={store.updateScene}
                  onAddScene={store.addScene}
                  onDeleteScene={(chapterId, sceneId) => setConfirmDelete({ type: 'scene', id: sceneId, chapterId })}
                  onOpenAiPanel={(view, id) => handleOpenAiPanel(view, id)}
                />
              </ResizablePanel>
            ) : selectedEdge && selectedEdge.type !== 'timeline' ? (
              <ResizablePanel>
                <EdgeDetail
                  edge={selectedEdge}
                  chapters={data.chapters}
                  acts={data.acts}
                  onClose={store.clearSelection}
                  onChangeType={(edgeId, newType) => {
                    const changed = store.changeEdgeType(edgeId, newType)
                    if (changed && newType === 'timeline') store.clearSelection()
                  }}
                  onDelete={(edgeId) => { store.deleteEdge(edgeId); store.clearSelection() }}
                  onUpdateEdge={store.updateEdge}
                />
              </ResizablePanel>
            ) : null
          )}

          {/* Detail panels - Character */}
          {views.activeViewId === 'narrative-char' && (
            selectedChar ? (
              <ResizablePanel>
                <CharacterDetail
                  character={selectedChar}
                  onClose={() => setSelectedCharacterId(null)}
                  onUpdateCharacter={store.updateCharacter}
                />
              </ResizablePanel>
            ) : selectedRelation ? (
              (() => {
                const srcChar = data.characters.find(c => c.id === selectedRelation.sourceId)
                const rel = srcChar?.relations.find(r => r.id === selectedRelation.relationId)
                const tgtChar = rel ? data.characters.find(c => c.id === rel.targetId) : undefined
                if (!srcChar || !rel || !tgtChar) return null
                return (
                  <ResizablePanel>
                    <CharacterEdgeDetail
                      source={srcChar}
                      target={tgtChar}
                      relation={rel}
                      onClose={() => setSelectedRelation(null)}
                      onDelete={() => { store.deleteRelation(selectedRelation.sourceId, selectedRelation.relationId); setSelectedRelation(null) }}
                      onUpdateRelation={store.updateRelation}
                    />
                  </ResizablePanel>
                )
              })()
            ) : null
          )}

          {/* Detail panels - Rhythm */}
          {views.activeViewId === 'narrative-rhythm' && selectedRhythmIndex !== null && (
            <ResizablePanel>
              {(() => {
                const point = data.rhythms[selectedRhythmIndex]
                if (!point) return null
                const ch = data.chapters[point.chapterIndex]
                const act = ch ? data.acts.find(a => a.id === ch.actId) : undefined
                return (
                  <RhythmDetail
                    point={point}
                    chapter={ch}
                    act={act}
                    wordCount={ch?.wordCount ?? 0}
                    onClose={() => setSelectedRhythmIndex(null)}
                  />
                )
              })()}
            </ResizablePanel>
          )}

          {/* Detail panels - Theme */}
          {views.activeViewId === 'narrative-theme' && selectedTheme && (
            <ResizablePanel>
              {(() => {
                const theme = data.themes[selectedTheme.themeIndex]
                if (!theme) return null
                const ch = selectedTheme.chapterIndex >= 0 ? data.chapters[selectedTheme.chapterIndex] : undefined
                return (
                  <ThemeDetail
                    theme={theme}
                    chapter={ch}
                    onClose={() => setSelectedTheme(null)}
                    onSaveNote={(note) => {
                      const t = data.themes[selectedTheme.themeIndex]
                      if (t && t.id) {
                        setData(d => d ? {
                          ...d,
                          themes: d.themes.map((th, i) => i === selectedTheme.themeIndex ? { ...th, note } : th)
                        } : d)
                        store.enqueueChange({ entity: 'themes', op: 'update', id: t.id, data: { note } })
                      }
                    }}
                  />
                )
              })()}
            </ResizablePanel>
          )}

          <ActionButtons
            onAIChat={handleAiChatOpen}
            onInspiration={() => setInspirationOpen(true)}
            onRhythmAnalysis={handleRhythmAnalysis}
            onConsistencyCheck={() => setConsistencyOpen(true)}
          />
        </div>

        {/* Drawer */}
        <LeftDrawer
          open={drawerOpen}
          acts={data.acts}
          chapters={data.chapters}
          onClose={() => setDrawerOpen(false)}
          onSelectChapter={handleChapterClick}
        />

        {/* Create theme dialog */}
        {showAddTheme && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowAddTheme(false)}>
            <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-96" onClick={e => e.stopPropagation()}>
              <h3 className="text-white font-semibold mb-4">新建主题</h3>
              <input placeholder="主题名称" value={newThemeName} onChange={e => setNewThemeName(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-white mt-2" />
              <div className="flex gap-2 mt-3">
                {["#d4a373","#6b7280","#ef4444","#3b82f6","#22c55e","#a855f7","#f97316","#ec4899"].map(c => (
                  <div key={c} onClick={() => setNewThemeColor(c)}
                    className={`w-7 h-7 rounded-full cursor-pointer border-2 ${newThemeColor === c ? 'border-white' : 'border-transparent'} hover:border-white`}
                    style={{ backgroundColor: c }} />
                ))}
              </div>
              <textarea placeholder="主题命题（可选）" value={newThemeProposition} onChange={e => setNewThemeProposition(e.target.value)}
                className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-white mt-3" rows={2} />
              <div className="flex gap-2 mt-4">
                <button onClick={() => { setShowAddTheme(false); setNewThemeName(''); setNewThemeColor('#d4a373'); setNewThemeProposition('') }}
                  className="flex-1 px-3 py-2 rounded bg-gray-700 text-gray-300 hover:bg-gray-600">取消</button>
                <button onClick={() => {
                  const t = store.addTheme(newThemeName || undefined, newThemeColor, newThemeProposition || undefined)
                  if (t) setSelectedTheme({ themeIndex: data.themes.length, chapterIndex: -1 })
                  setShowAddTheme(false)
                  setNewThemeName('')
                  setNewThemeColor('#d4a373')
                  setNewThemeProposition('')
                }} className="flex-1 px-3 py-2 rounded bg-blue-600 text-white hover:bg-blue-500">确定</button>
              </div>
            </div>
          </div>
        )}

        {/* Delete theme confirmation */}
        {deleteThemeId !== null && (() => {
          const theme = data.themes.find(t => t.id === deleteThemeId)
          return (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setDeleteThemeId(null)}>
              <div className="bg-gray-900 border border-gray-700 rounded-xl p-6 w-80" onClick={e => e.stopPropagation()}>
                <h3 className="text-white font-semibold mb-2">删除主题</h3>
                <p className="text-sm text-gray-400 mb-4">确定要删除主题「{theme?.name}」吗？</p>
                <div className="flex gap-2 justify-end">
                  <button onClick={() => setDeleteThemeId(null)}
                    className="px-3 py-2 rounded bg-gray-700 text-gray-300 hover:bg-gray-600 text-sm">取消</button>
                  <button onClick={() => {
                    store.deleteTheme(deleteThemeId)
                    setSelectedTheme(null)
                    setDeleteThemeId(null)
                  }} className="px-3 py-2 rounded bg-red-600 text-white hover:bg-red-500 text-sm">删除</button>
                </div>
              </div>
            </div>
          )
        })()}

        {/* Modals */}
        <PreviewModal open={previewOpen} chapters={getCompletedChain(data.chapters, data.edges, data.acts).flat()} acts={data.acts} onClose={() => setPreviewOpen(false)} />

        <GlobalSettingsModal
          open={globalSettingsOpen}
          initialText={data.globalSettings}
          onSave={(text) => store.saveGlobalSettings(text)}
          onClose={() => setGlobalSettingsOpen(false)}
        />

        {editingScene && (
          <SceneEditor
            projectId={projectId}
            scene={editingScene}
            chapterTitle={data.chapters.find(c => c.scenes.some(s => s.id === editingScene.id))?.title ?? ''}
            onClose={() => setEditingScene(null)}
            onSaved={handleSceneSaved}
            onOpenAiPanel={(view, id) => handleOpenAiPanel(view, id)}
          />
        )}

        {aiChatOpen && (
          <AiPanel
            projectId={projectId}
            onClose={() => setAiChatOpen(false)}
            onProjectUpdated={handleProjectUpdated}
            contextView={aiContextView}
            contextId={aiContextId}
          />
        )}

        {inspirationOpen && (
          <InspirationModal
            onClose={() => setInspirationOpen(false)}
            onApplyStarter={(title: string) => {
              if (store.data) store.setData({ ...store.data, projectTitle: title })
              setInspirationOpen(false)
            }}
          />
        )}

        {consistencyOpen && (
          <ConsistencyCheckModal
            projectId={projectId}
            onClose={() => setConsistencyOpen(false)}
            onNavigate={(location) => {
              setConsistencyOpen(false)
              if (location.scene_id) {
                const chapter = data.chapters?.find(c => c.scenes.some(s => s.id === location.scene_id))
                const scene = chapter?.scenes.find(s => s.id === location.scene_id)
                if (scene) {
                  if (chapter) {
                    setSelectedChapter(chapter)
                    views.switchView('narrative-plot')
                  }
                  setEditingScene(scene)
                }
              } else if (location.chapter_id) {
                const idx = data.chapters?.findIndex(c => c.id === location.chapter_id)
                if (idx >= 0 && data.chapters) {
                  setSelectedChapter(data.chapters[idx])
                  views.switchView('narrative-plot')
                }
              }
            }}
          />
        )}

        {/* Bottom nav */}
        <BottomNav
          activeViewId={views.activeViewId}
          onSwitchView={views.switchView}
          onPreview={() => setPreviewOpen(true)}
          onExport={handleExport}
          onGlobalSetting={() => setGlobalSettingsOpen(true)}
        />
      <ConfirmDialog
        open={confirmDelete !== null}
        title={confirmDelete?.type === 'act' ? '删除幕' : confirmDelete?.type === 'scene' ? '删除场景' : '删除章'}
        message={
          confirmDelete?.type === 'act'
            ? "确定要删除「" + (data.acts.find(a => a.id === confirmDelete.id)?.name ?? '') + "」吗？该幕下的所有章节和连线将一并删除。"
            : confirmDelete?.type === 'scene'
            ? "确定要删除该场景吗？"
            : "确定要删除「" + (data.chapters.find(c => c.id === confirmDelete?.id)?.title ?? '') + "」吗？"
        }
        onConfirm={() => {
          if (!confirmDelete) return
          if (confirmDelete.type === 'act') { store.deleteAct(confirmDelete.id); setSelectedActId(null); setSelectedChapter(null) }
          else if (confirmDelete.type === 'scene') {
            store.deleteScene(confirmDelete.chapterId!, confirmDelete.id)
          }
          else { store.deleteChapter(confirmDelete.id); setSelectedChapter(null) }
          setConfirmDelete(null)
        }}
        onCancel={() => setConfirmDelete(null)}
      />
      </div>
  )
}
