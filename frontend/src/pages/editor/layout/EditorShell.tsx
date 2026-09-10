import { useState, useCallback, useEffect, useRef } from 'react'
import SideNav from './SideNav'
import CanvasPanel from './CanvasPanel'
import PanelDock, { DockGrip } from './PanelDock'
import type { DockPanelMeta } from './PanelDock'
import { containsLeaf, leafIds, makeLeaf, makeRow, prune } from './splitTree'
import type { TreeNode } from './splitTree'
import OutlinePanel from './OutlinePanel'
import ActionButtons from './ActionButtons'
import PlotCanvas from '../views/plot/PlotCanvas'
import PlotToolbar from '../views/plot/PlotToolbar'
import ChapterDetail from '../views/plot/ChapterDetail'
import ActDetail from '../views/plot/ActDetail'
import EdgeDetail from '../views/plot/EdgeDetail'
import CharCanvas from '../views/character/CharCanvas'
import CharacterDetail from '../views/character/CharacterDetail'
import CharacterEdgeDetail from '../views/character/CharacterEdgeDetail'
import PreviewPanel from '../modals/PreviewPanel'
import SceneEditor from '../modals/SceneEditor'
import ChapterGoalEditor from '../modals/ChapterGoalEditor'
import GlobalSettingsPanel from '../modals/GlobalSettingsPanel'
import AiPanel, { useAiChat } from '../modals/AiChatPanel'
import InspirationModal from '../modals/InspirationModal'
import { useEditorViews } from '../hooks/useEditorViews'
import { useSessionState } from '../../../hooks/useSessionState'
import { useEditorStore } from '../data/editorStore'
import { loadEditorData, saveSceneContent } from '../../../api/editor'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import DetailPanel from '../components/DetailPanel'
import type { Chapter, EdgeType } from '../types'
import { orderChapters, orderActChapters } from '../data/orderUtils'

const DOCK_TREE_KEY = 'aistorycad_dock_tree'
const DOCK_PANEL_IDS = ['outline', 'canvas', 'detail', 'ai']

/** share of the container the outline takes when it is added to an older layout */
const OUTLINE_WEIGHT = 0.2

/** default layout: one row, the outline on the left, canvas widest */
function defaultDockTree(): TreeNode {
  // with the outline closed the rest falls back to 0.45 / 0.275 / 0.275
  return makeRow(DOCK_PANEL_IDS, [OUTLINE_WEIGHT, 0.36, 0.22, 0.22])
}

/** put the outline at the left of the root, shrinking the others proportionally */
function prependOutline(node: TreeNode): TreeNode {
  if (containsLeaf(node, 'outline')) return node
  if (node.kind === 'split' && node.dir === 'row') {
    return {
      kind: 'split',
      dir: 'row',
      children: [makeLeaf('outline'), ...node.children],
      weights: [OUTLINE_WEIGHT, ...node.weights.map(w => w * (1 - OUTLINE_WEIGHT))],
    }
  }
  return {
    kind: 'split',
    dir: 'row',
    children: [makeLeaf('outline'), node],
    weights: [OUTLINE_WEIGHT, 1 - OUTLINE_WEIGHT],
  }
}

/** restore the saved split layout, falling back to the default when it is stale */
function loadDockTree(): TreeNode {
  try {
    const raw = localStorage.getItem(DOCK_TREE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as TreeNode
      if (parsed && typeof parsed === 'object' && 'kind' in parsed) {
        const pruned = prune(parsed, id => DOCK_PANEL_IDS.includes(id))
        if (pruned && leafIds(pruned).length === DOCK_PANEL_IDS.length) return pruned
        // layouts saved before the outline became a panel hold one leaf less
        if (pruned && leafIds(pruned).length === DOCK_PANEL_IDS.length - 1 && !containsLeaf(pruned, 'outline')) {
          return prependOutline(pruned)
        }
      }
    }
  } catch { /* ignore */ }
  return defaultDockTree()
}

export default function EditorShell({ projectId }: { projectId: string }) {
  const views = useEditorViews()
  const [outlineOpen, setOutlineOpen] = useState(false)
  // 预览 / 导出共用的定稿过滤（会话内保留，切换面板不丢）
  const [finalOnly, setFinalOnly] = useSessionState<boolean>('editor.finalOnly', false)
  const [selectedActId, setSelectedActId] = useState<string | null>(null)
  const [selectedChapter, setSelectedChapter] = useState<Chapter | null>(null)
  const [connectionMode, setConnectionMode] = useState<'all' | EdgeType>('all')
  // What the detail panel shows instead of the normal selection view: a
  // full-panel editor, the finished-content preview or the global settings.
  // Cleared on save / cancel or when the selection moves on.
  const [detailMode, setDetailMode] = useState<
    | { kind: 'chapter-goal'; chapterId: string }
    | { kind: 'scene'; sceneId: string }
    | { kind: 'preview' }
    | { kind: 'settings' }
    | null
  >(null)
  const [confirmDelete, setConfirmDelete] = useState<{ type: 'act' | 'chapter' | 'scene'; id: string; chapterId?: string } | null>(null)
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null)
  const [selectedRelation, setSelectedRelation] = useState<{ sourceId: string; relationId: string } | null>(null)
  const [aiChatOpen, setAiChatOpen] = useState(false)
  const [aiContextView, setAiContextView] = useState<string>('chat')
  const [aiContextId, setAiContextId] = useState<string | undefined>(undefined)
  const [inspirationOpen, setInspirationOpen] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  // Which view the current selection belongs to. Independent of whether the
  // canvas panel is open, so the detail panel keeps its content when the canvas
  // is closed and can be driven from the outline instead.
  const [selectionView, setSelectionView] = useState<string>('narrative-plot')
  const [floatingState, setFloatingState] = useState<Record<string, boolean>>({})
  const [dockTree, setDockTree] = useState<TreeNode>(loadDockTree)
  // Lives here, not in the panel: floating the panel remounts it, and the
  // conversation, draft and streaming reply must survive that.
  const aiChat = useAiChat(projectId, aiContextView, aiContextId)

  const { addToast } = useToast()

  const store = useEditorStore(projectId, useCallback((msg: string) => {
    addToast(msg, 'error')
  }, [addToast]))
  const data = store.data
  const [layoutKey, setLayoutKey] = useState(0)
  const [relinking, setRelinking] = useState(false)
  const handleAutoLayout = useCallback(() => setLayoutKey(k => k + 1), [])

  // 顶部栏「重连时序」：手动把当前章节顺序重新投影成一条时序直链，
  // 不必等下一次同步、也不用切视图才知道连线有没有跟上。
  const handleRelinkTimeline = useCallback(async () => {
    setRelinking(true)
    try {
      const result = await store.relinkTimeline()
      if (result.created === 0 && result.deleted === 0) addToast('时序线已是最新', 'info')
      else addToast(`时序线已重连：新增 ${result.created} 条，清理 ${result.deleted} 条`, 'success')
    } catch (e) {
      addToast((e as Error)?.message || '重连时序线失败', 'error')
    } finally {
      setRelinking(false)
    }
  }, [store, addToast])

  const handleActClick = useCallback((actId: string) => {
    if (!actId) { setSelectedActId(null); store.clearSelection(); return }
    setDetailMode(null)
    setSelectionView('narrative-plot')
    setSelectedActId(actId); setSelectedChapter(null); store.selectNode('act', actId)
  }, [store])

  const handleChapterClick = useCallback((chapterId: string) => {
    if (!data) return
    const ch = data.chapters.find(c => c.id === chapterId)
    if (!ch) return
    setDetailMode(null)
    setSelectionView('narrative-plot')
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
  }, [setData])

  const handleChapterGoalSave = useCallback((chapterId: string, goal: string) => {
    store.updateChapter(chapterId, { goal })
    setSelectedChapter(prev => (prev && prev.id === chapterId ? { ...prev, goal } : prev))
  }, [store])

  const handleSceneGoalSave = useCallback((sceneId: string, summary: string) => {
    const chapter = store.data?.chapters.find(c => c.scenes.some(s => s.id === sceneId))
    if (chapter) {
      store.updateScene(chapter.id, sceneId, { summary })
    }
  }, [store])

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

  // The detail container stays open even when its content is cleared (e.g. the
  // canvas was closed), so its visibility is explicit state instead of being
  // derived from "is something selected".
  const hasSelection = store.selection.id !== null
    || selectedCharacterId !== null
    || selectedRelation !== null
  useEffect(() => {
    if (hasSelection) setDetailOpen(true)
  }, [hasSelection])

  // Moving to another act / chapter / node (or switching canvas) closes the
  // full-panel editor and hands the detail panel back to the selection view.
  const detailTargetKey = [
    selectionView,
    selectedChapter?.id ?? '',
    selectedActId ?? '',
    selectedCharacterId ?? '',
    selectedRelation ? `${selectedRelation.sourceId}|${selectedRelation.relationId}` : '',
    store.selection.type,
    store.selection.id ?? '',
  ].join('|')
  useEffect(() => {
    setDetailMode(null)
  }, [detailTargetKey])

  // Debounced so a divider drag does not write to localStorage on every frame.
  useEffect(() => {
    const timer = setTimeout(() => {
      try { localStorage.setItem(DOCK_TREE_KEY, JSON.stringify(dockTree)) } catch { /* ignore */ }
    }, 300)
    return () => clearTimeout(timer)
  }, [dockTree])

  // The dock owns the float state of every panel: while floating a panel is
  // moved out of the split tree, which remounts it, so the flag cannot live in
  // the panel itself.
  const handleFloatChange = useCallback((id: string, floating: boolean) => {
    setFloatingState(prev => (prev[id] === floating ? prev : { ...prev, [id]: floating }))
  }, [])

  // Closing the canvas deselects the side nav entry but keeps the detail panel
  // open with cleared content.
  // Switching the canvas also retargets the detail panel at that view's own
  // selection; closing the canvas deliberately does not.
  const handleSwitchView = useCallback((viewId: string) => {
    views.switchView(viewId)
    setSelectionView(viewId)
  }, [views])

  const closeCanvas = useCallback(() => views.switchView(null), [views])

  // Closing the panel clears the content and hides the container; whatever was
  // selected on the canvas is deselected too.
  const closeDetail = useCallback(() => {
    setSelectedActId(null)
    setSelectedChapter(null)
    setSelectedRelation(null)
    setSelectedCharacterId(null)
    store.clearSelection()
    setDetailMode(null)
    setDetailOpen(false)
  }, [store])

  // Side-nav entries that used to open a modal: they now open the detail panel
  // on that page instead.
  const openPreview = useCallback(() => {
    setDetailOpen(true)
    setDetailMode({ kind: 'preview' })
  }, [])

  const openGlobalSettings = useCallback(() => {
    setDetailOpen(true)
    setDetailMode({ kind: 'settings' })
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
            onSelectNode={(type, id) => { setSelectionView('narrative-plot'); store.selectNode(type, id) }}
            onSelectEdge={(edgeId: string) => {
              setSelectionView('narrative-plot')
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
            onSelectCharacter={(id) => { setSelectionView('narrative-char'); setSelectedCharacterId(id); setSelectedRelation(null) }}
            onSelectRelation={(sourceId, relationId) => { setSelectionView('narrative-char'); setSelectedRelation({ sourceId, relationId }); setSelectedCharacterId(null) }}
            onClearSelection={() => { setSelectedCharacterId(null); setSelectedRelation(null) }}
            onAddCharacter={() => { const ch = store.addCharacter(); setSelectedCharacterId(ch.id) }}
            onDeleteCharacter={(id) => { store.deleteCharacter(id); setSelectedCharacterId(null) }}
            onAddRelation={(sourceId, targetId) => { store.addRelation(sourceId, targetId) }}
            onDeleteRelation={(characterId, relationId) => { store.deleteRelation(characterId, relationId); setSelectedRelation(null) }}
          />
        )
      default:
        return <div className="flex items-center justify-center h-full text-gray-500">选择视图</div>
    }
  }

  // The content of the shared right-hand panel. Both canvases feed this single
  // slot, so selecting a plot node and selecting a character node render into
  // the same container — only the content differs.
  const renderDetail = () => {
    if (selectionView === 'narrative-plot') {
      if (selectedAct) {
        return (
          <ActDetail
            act={selectedAct}
            chapters={orderActChapters(data.chapters.filter(c => c.actId === selectedActId))}
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
            onOpenSceneEditor={(scene) => setDetailMode({ kind: 'scene', sceneId: scene.id })}
            onUpdateAct={store.updateAct}
            onUpdateScene={store.updateScene}
            onAddChapter={store.addChapter}
            onMoveChapter={store.moveChapter}
            onDeleteScene={(chapterId, sceneId) => setConfirmDelete({ type: 'scene', id: sceneId, chapterId })}
          />
        )
      }

      if (activeChapter) {
        return (
          <ChapterDetail
            chapter={activeChapter}
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
            onChapterSave={handleChapterGoalSave}
            onOpenSceneEditor={(scene) => setDetailMode({ kind: 'scene', sceneId: scene.id })}
            onEditGoal={(chapter) => setDetailMode({ kind: 'chapter-goal', chapterId: chapter.id })}
            onUpdateChapter={store.updateChapter}
            onUpdateScene={store.updateScene}
            onAddScene={store.addScene}
            onDeleteScene={(chapterId, sceneId) => setConfirmDelete({ type: 'scene', id: sceneId, chapterId })}
            onOpenAiPanel={(view, id) => handleOpenAiPanel(view, id)}
          />
        )
      }

      if (selectedEdge && selectedEdge.type !== 'timeline') {
        return (
          <EdgeDetail
            edge={selectedEdge}
            chapters={data.chapters}
            acts={data.acts}
            onChangeType={(edgeId, newType) => {
              const changed = store.changeEdgeType(edgeId, newType)
              if (changed && newType === 'timeline') store.clearSelection()
            }}
            onDelete={(edgeId) => { store.deleteEdge(edgeId); store.clearSelection() }}
            onUpdateEdge={store.updateEdge}
          />
        )
      }

      return null
    }

    if (selectionView === 'narrative-char') {
      if (selectedChar) {
        return (
          <CharacterDetail
            character={selectedChar}
            onUpdateCharacter={store.updateCharacter}
          />
        )
      }

      if (selectedRelation) {
        const srcChar = data.characters.find(c => c.id === selectedRelation.sourceId)
        const rel = srcChar?.relations.find(r => r.id === selectedRelation.relationId)
        const tgtChar = rel ? data.characters.find(c => c.id === rel.targetId) : undefined
        if (!srcChar || !rel || !tgtChar) return null
        return (
          <CharacterEdgeDetail
            source={srcChar}
            target={tgtChar}
            relation={rel}
            onDelete={() => { store.deleteRelation(selectedRelation.sourceId, selectedRelation.relationId); setSelectedRelation(null) }}
            onUpdateRelation={store.updateRelation}
          />
        )
      }
    }

    return null
  }

  const handleExport = () => {
    const ordered = orderChapters(data.chapters, data.acts)
    const allChapters = finalOnly ? ordered.filter(ch => ch.status === 'final') : ordered
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

  const canvasOpen = views.activeViewId !== null

  // A pending full-panel editor replaces the normal detail content; save /
  // cancel clears it and the panel falls back to the selection view.
  const editingScene = detailMode?.kind === 'scene'
    ? data.chapters.flatMap(c => c.scenes).find(s => s.id === detailMode.sceneId) ?? null
    : null
  const goalChapter = detailMode?.kind === 'chapter-goal'
    ? data.chapters.find(c => c.id === detailMode.chapterId) ?? null
    : null
  const previewMode = detailMode?.kind === 'preview'
  const settingsMode = detailMode?.kind === 'settings'
  // 预览与导出共用同一个过滤：定稿模式只保留 status === 'final' 的章节。
  const orderedChapters = orderChapters(data.chapters, data.acts)
  const previewChapters = finalOnly
    ? orderedChapters.filter(ch => ch.status === 'final')
    : orderedChapters

  const detailPanel = editingScene ? (
    <SceneEditor
      projectId={projectId}
      scene={editingScene}
      chapterTitle={data.chapters.find(c => c.scenes.some(s => s.id === editingScene.id))?.title ?? ''}
      onClose={() => setDetailMode(null)}
      onSaved={handleSceneSaved}
      onOpenAiPanel={(view, id) => handleOpenAiPanel(view, id)}
      onSaveGoal={(summary) => handleSceneGoalSave(editingScene.id, summary)}
    />
  ) : goalChapter ? (
    <ChapterGoalEditor
      chapter={goalChapter}
      onSave={(goal) => handleChapterGoalSave(goalChapter.id, goal)}
      onClose={() => setDetailMode(null)}
    />
  ) : previewMode ? (
    <PreviewPanel
      chapters={previewChapters}
      acts={data.acts}
      finalOnly={finalOnly}
      onFinalOnlyChange={setFinalOnly}
    />
  ) : settingsMode ? (
    <GlobalSettingsPanel
      initialText={data.globalSettings}
      onSave={(text) => store.saveGlobalSettings(text)}
      onClose={() => setDetailMode(null)}
    />
  ) : renderDetail()

  const detailLabel = editingScene ? '场景编辑'
    : goalChapter ? '本章目标'
    : previewMode ? '预览'
    : settingsMode ? '全局设定'
    : '详情'

  // Everything the dock may host. Panels that are closed are simply absent; the
  // split tree keeps their slot, so reopening them restores the same place.
  const dockPanels: Record<string, DockPanelMeta | undefined> = {}

  if (outlineOpen) {
    dockPanels.outline = {
      title: '大纲',
      minW: 220,
      minH: 240,
      node: (
        <OutlinePanel
          acts={data.acts}
          chapters={data.chapters}
          selectedActId={selectedActId}
          selectedChapterId={activeChapter?.id ?? null}
          grip={<DockGrip id="outline" />}
          floating={floatingState.outline === true}
          onFloatingChange={(v) => handleFloatChange('outline', v)}
          onClose={() => setOutlineOpen(false)}
          onSelectAct={(id) => handleActClick(id)}
          onSelectChapter={(id) => handleChapterClick(id)}
          onMoveChapter={store.moveChapter}
        />
      ),
    }
  }

  if (canvasOpen) {
    dockPanels.canvas = {
      title: `${views.activeView?.label ?? ''}幕布`,
      minW: 420,
      minH: 240,
      node: (
        <CanvasPanel
          label={`${views.activeView?.label ?? ''}幕布`}
          grip={<DockGrip id="canvas" />}
          floating={floatingState.canvas === true}
          onFloatingChange={(v) => handleFloatChange('canvas', v)}
          onClose={closeCanvas}
          toolbar={views.activeViewId === 'narrative-plot' ? (
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
              onRelinkTimeline={handleRelinkTimeline}
              relinking={relinking}
            />
          ) : null}
        >
          {renderCanvas()}
        </CanvasPanel>
      ),
    }
  }

  if (detailOpen) {
    dockPanels.detail = {
      title: '详情',
      minW: 280,
      minH: 240,
      node: (
        <DetailPanel
          label={detailLabel}
          grip={<DockGrip id="detail" />}
          floating={floatingState.detail === true}
          onFloatingChange={(v) => handleFloatChange('detail', v)}
          onClose={closeDetail}
        >
          {detailPanel}
        </DetailPanel>
      ),
    }
  }

  if (aiChatOpen) {
    dockPanels.ai = {
      title: 'AI 对话',
      minW: 300,
      minH: 260,
      node: (
        <AiPanel
          chat={aiChat}
          projectId={projectId}
          grip={<DockGrip id="ai" />}
          floating={floatingState.ai === true}
          onFloatingChange={(v) => handleFloatChange('ai', v)}
          onClose={() => setAiChatOpen(false)}
          onProjectUpdated={handleProjectUpdated}
          contextView={aiContextView}
        />
      ),
    }
  }
  return (
    <div className="h-screen flex bg-gray-950 text-gray-100 overflow-hidden select-none">
      {/* Icon-only left side nav: views, content management, save status, outline, settings */}
      <SideNav
        activeViewId={views.activeViewId}
        onSwitchView={handleSwitchView}
        onPreview={openPreview}
        onExport={handleExport}
        finalOnly={finalOnly}
        onGlobalSetting={openGlobalSettings}
        outlineOpen={outlineOpen}
        onOutline={() => setOutlineOpen(v => !v)}
        dirty={store.dirty}
        saving={store.saving}
        onSave={() => store.flushChanges()}
      />

      {/* The dock: every panel is a sibling here, so docked panels squeeze each
          other and a floating panel leaves the flow entirely. */}
      <PanelDock tree={dockTree} onTreeChange={setDockTree} panels={dockPanels} floating={floatingState} />

      <ActionButtons
        onAIChat={handleAiChatOpen}
        onInspiration={() => setInspirationOpen(true)}
      />

      {/* Modals */}

      {inspirationOpen && (
        <InspirationModal
          onClose={() => setInspirationOpen(false)}
          onApplyStarter={(title: string) => {
            if (store.data) store.setData({ ...store.data, projectTitle: title })
            setInspirationOpen(false)
          }}
        />
      )}

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
