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
import SetupPanel from '../views/plot/SetupPanel'
import SetupDetail from '../views/plot/SetupDetail'
import CharCanvas from '../views/character/CharCanvas'
import CharacterDetail from '../views/character/CharacterDetail'
import CharacterEdgeDetail from '../views/character/CharacterEdgeDetail'
import PreviewPanel from '../modals/PreviewPanel'
import ChapterTextEditor from '../modals/ChapterTextEditor'
import type { ChapterTextField } from '../modals/ChapterTextEditor'
import GlobalSettingsPanel from '../modals/GlobalSettingsPanel'
import AiPanel, { useAiChat } from '../modals/AiChatPanel'
import InspirationModal from '../modals/InspirationModal'
import { useEditorViews } from '../hooks/useEditorViews'
import { useSessionState } from '../../../hooks/useSessionState'
import { useEditorStore } from '../data/editorStore'
import { loadAllChapterContents, loadChapterContent, loadEditorData, saveChapterContent } from '../../../api/editor'
import { useToast } from '../components/Toast'
import ConfirmDialog from '../components/ConfirmDialog'
import DetailPanel from '../components/DetailPanel'
import type { Chapter } from '../types'
import { orderChapters, orderActChapters } from '../data/orderUtils'

const DOCK_TREE_KEY = 'aistorycad_dock_tree'
const DOCK_PANEL_IDS = ['outline', 'canvas', 'detail', 'ai']

/**
 * AI 面板开着还是关着，按项目记在浏览器里：刷新页面之后现场还在，
 * 而不是每次都要重新点开（面板里还会自己回到上次那个会话）。
 */
const AI_PANEL_KEY = 'aistorycad_ai_panel'

function readStoredPanelOpen(projectId: string): boolean {
  try {
    return localStorage.getItem(`${AI_PANEL_KEY}:${projectId}`) === '1'
  } catch {
    return false
  }
}

function storePanelOpen(projectId: string, open: boolean) {
  try {
    localStorage.setItem(`${AI_PANEL_KEY}:${projectId}`, open ? '1' : '0')
  } catch {
    // 隐私模式 / 存储被禁用：记不住就算了
  }
}

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
  // What the detail panel shows instead of the normal selection view: a full-panel
  // field editor (本章要写什么 / 本章写成了什么 / 正文), the finished-content
  // preview, the setups list or the global settings.
  // Cleared on save / cancel or when the selection moves on.
  const [detailMode, setDetailMode] = useState<
    | { kind: 'chapter-field'; chapterId: string; field: ChapterTextField }
    | { kind: 'preview' }
    | { kind: 'settings' }
    | { kind: 'setups' }
    | null
  >(null)
  const [confirmDelete, setConfirmDelete] = useState<{ type: 'act' | 'chapter'; id: string } | null>(null)
  const [selectedCharacterId, setSelectedCharacterId] = useState<string | null>(null)
  const [selectedRelation, setSelectedRelation] = useState<{ sourceId: string; relationId: string } | null>(null)
  // 伏笔不是画布节点（数据层是一对配对引用），所以选中状态和人物/关系一样放这里
  const [selectedSetupId, setSelectedSetupId] = useState<string | null>(null)
  const [aiChatOpen, setAiChatOpen] = useState(() => readStoredPanelOpen(projectId))
  const [aiContextView, setAiContextView] = useState<string>('chat')
  const [aiContextId, setAiContextId] = useState<string | undefined>(undefined)
  const [inspirationOpen, setInspirationOpen] = useState(false)
  // 进入工作台默认打开：详情页是这一页的主视图之一，先摆出来用户才知道它在哪、
  // 里面显示选中的东西。之后怎么开关由用户决定——选中不再自动弹（见下面的注释）。
  const [detailOpen, setDetailOpen] = useState(true)
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
  const handleAutoLayout = useCallback(() => setLayoutKey(k => k + 1), [])

  const handleActClick = useCallback((actId: string) => {
    if (!actId) { setSelectedActId(null); store.clearSelection(); return }
    setDetailMode(null)
    setSelectionView('narrative-plot')
    setSelectedSetupId(null)
    setSelectedActId(actId); setSelectedChapter(null); store.selectNode('act', actId)
  }, [store])

  const handleChapterClick = useCallback((chapterId: string) => {
    if (!data) return
    const ch = data.chapters.find(c => c.id === chapterId)
    if (!ch) return
    setDetailMode(null)
    setSelectionView('narrative-plot')
    setSelectedSetupId(null)
    setSelectedChapter(ch); setSelectedActId(null); store.selectNode('chapter', chapterId)
  }, [data, store])

  /** 点情节幕布上的伏笔线：选中那条伏笔，详情面板显示它。 */
  const handleSetupSelect = useCallback((setupId: string) => {
    setDetailMode(null)
    setSelectionView('narrative-plot')
    setSelectedActId(null)
    setSelectedChapter(null)
    setSelectedCharacterId(null)
    setSelectedRelation(null)
    store.clearSelection()
    setSelectedSetupId(setupId)
  }, [store])

  /** 幕布右键「编辑本章」是**显式**动作：选中它，并把详情页打开。 */
  const handleEditChapter = useCallback((chapterId: string) => {
    handleChapterClick(chapterId)
    setDetailOpen(true)
  }, [handleChapterClick])

  /**
   * 全屏字段编辑器保存。goal / result 只是 store 上的字段；正文要先写服务端再改
   * 本地——正文是延迟列，本地那份只是副本。
   */
  const handleFieldSave = useCallback(async (text: string) => {
    if (detailMode?.kind !== 'chapter-field') return
    const { chapterId, field } = detailMode
    if (field === 'body') {
      await saveChapterContent(projectId, chapterId, text)
      store.setChapterContent(chapterId, text)
      return
    }
    if (field === 'goal') store.updateChapter(chapterId, { goal: text })
    else store.updateChapter(chapterId, { result: text })
    setSelectedChapter(prev => (prev && prev.id === chapterId
      ? (field === 'goal' ? { ...prev, goal: text } : { ...prev, result: text })
      : prev))
  }, [detailMode, projectId, store])

  /**
   * 正文全屏编辑前先确认正文已经取回来：正文是延迟列，批量请求失败时 store 里
   * 根本没有这一章的正文，编辑器会拿着空字符串去保存——那等于清空一章。
   */
  const handleOpenBodyEditor = useCallback(async (chapter: Chapter) => {
    if (!Object.prototype.hasOwnProperty.call(data?.chapterContents ?? {}, chapter.id)) {
      try {
        const text = await loadChapterContent(projectId, chapter.id)
        store.setChapterContent(chapter.id, text)
      } catch {
        addToast('正文读取失败，暂时不能全屏编辑', 'error')
        return
      }
    }
    setDetailMode({ kind: 'chapter-field', chapterId: chapter.id, field: 'body' })
  }, [data, projectId, store, addToast])

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

  // AI 写完项目数据后重载：正文（chapters.content）是延迟列，editor-data 不带它，
  // 所以两个请求一起发，否则新写入的正文在编辑器里看不到。
  const handleProjectUpdated = useCallback(() => {
    store.flushChanges().then(async ok => {
      if (!ok) {
        addToast('本地修改尚未保存，无法安全重载 AI 写入数据，请稍后再试', 'warning')
        return
      }
      try {
        const [fresh, contents] = await Promise.all([
          loadEditorData(projectId),
          loadAllChapterContents(projectId),
        ])
        store.setData({ ...fresh, chapterContents: contents })
      } catch (err) {
        console.error('Failed to reload project data:', err)
        addToast('重载项目数据失败', 'error')
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

  // 详情页**不再随幕布上的选择自动弹出**：点章节节点 / 人物节点 / 幕框、点大纲一行
  // 都只是"选中"（幕布上高亮），要看详情走左导航的「详情页」，或用幕布右键的
  // 「编辑本章」。所以面板的可见性是自己的一份状态，不从"有没有选东西"推出来。
  // 全局设定 / 已完成内容 / 伏笔仍然自己打开这个面板——那是导航动作，不是自动弹。

  // Moving to another act / chapter / node (or switching canvas) closes the
  // full-panel editor and hands the detail panel back to the selection view.
  const detailTargetKey = [
    selectionView,
    selectedChapter?.id ?? '',
    selectedActId ?? '',
    selectedCharacterId ?? '',
    selectedRelation ? `${selectedRelation.sourceId}|${selectedRelation.relationId}` : '',
    selectedSetupId ?? '',
    store.selection.type,
    store.selection.id ?? '',
  ].join('|')
  useEffect(() => {
    setDetailMode(null)
  }, [detailTargetKey])

  useEffect(() => {
    storePanelOpen(projectId, aiChatOpen)
  }, [aiChatOpen, projectId])

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
    setSelectedSetupId(null)
    store.clearSelection()
    setDetailMode(null)
    setDetailOpen(false)
  }, [store])

  /**
   * 左导航「详情页」：总是落在**详情**（当前选中项那一页）上——面板正开着预览 /
   * 伏笔 / 全局设定时点它也回到详情。已经停在详情页上时再点一次就关掉，和面板
   * 自己的 ✕ 是同一套语义。
   */
  const handleDetailNav = useCallback(() => {
    if (detailOpen && detailMode === null) { closeDetail(); return }
    setDetailMode(null)
    setDetailOpen(true)
  }, [detailOpen, detailMode, closeDetail])

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

  const openSetups = useCallback(() => {
    setDetailOpen(true)
    setDetailMode({ kind: 'setups' })
  }, [])

  if (store.loading) return <div className="h-screen bg-gray-950 flex items-center justify-center text-gray-500 text-sm">加载项目数据...</div>
  if (store.error) return <div className="h-screen bg-gray-950 flex items-center justify-center text-red-400 text-sm">{store.error}</div>
  if (!data) return <div className="h-screen bg-gray-950 flex items-center justify-center text-gray-500 text-sm">暂无数据</div>

  const selectedAct = selectedActId ? data.acts.find(a => a.id === selectedActId) ?? null : null
  const activeChapter = selectedChapter ? data.chapters.find(c => c.id === selectedChapter.id) ?? null : null
  const selectedChar = selectedCharacterId ? data.characters.find(c => c.id === selectedCharacterId) ?? null : null
  const selectedSetup = selectedSetupId ? data.setups.find(s => s.id === selectedSetupId) ?? null : null

  const renderCanvas = () => {
    switch (views.activeViewId) {
      case 'narrative-plot':
        return (
          <PlotCanvas
            chapters={data.chapters}
            acts={data.acts}
            setups={data.setups}
            contents={data.chapterContents}
            onChapterClick={handleChapterClick}
            onEditChapter={handleEditChapter}
            onActClick={handleActClick}
            onAddChapter={store.addChapter}
            onDeleteChapter={(id) => setConfirmDelete({ type: 'chapter', id })}
            onAddAct={store.addAct}
            onDeleteAct={(id) => setConfirmDelete({ type: 'act', id })}
            onActResize={store.resizeAct}
            selection={store.selection}
            selectedSetupId={selectedSetupId}
            onSelectNode={(type, id) => { setSelectionView('narrative-plot'); store.selectNode(type, id) }}
            onSelectSetup={handleSetupSelect}
            onClearSelection={() => { setSelectedSetupId(null); store.clearSelection() }}
            resetKey={layoutKey}
          />
        )
      case 'narrative-char':
        return (
          <CharCanvas
            characters={data.characters}
            selection={{ type: selectedCharacterId ? 'character' : selectedRelation ? 'relation' : null, id: selectedCharacterId ?? (selectedRelation ? `${selectedRelation.sourceId}|${selectedRelation.relationId}` : null) }}
            onSelectCharacter={(id) => { setSelectionView('narrative-char'); setSelectedSetupId(null); setSelectedCharacterId(id); setSelectedRelation(null) }}
            onSelectRelation={(sourceId, relationId) => { setSelectionView('narrative-char'); setSelectedSetupId(null); setSelectedRelation({ sourceId, relationId }); setSelectedCharacterId(null) }}
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
      if (selectedSetup) {
        return (
          <SetupDetail
            key={selectedSetup.id}
            setup={selectedSetup}
            chapters={orderChapters(data.chapters, data.acts)}
            acts={data.acts}
            onUpdate={store.updateSetup}
            onDelete={async (id) => { await store.deleteSetup(id); setSelectedSetupId(null) }}
          />
        )
      }

      if (selectedAct) {
        return (
          <ActDetail
            act={selectedAct}
            chapters={orderActChapters(data.chapters.filter(c => c.actId === selectedActId))}
            contents={data.chapterContents}
            onSelectChapter={(chId) => { setSelectedActId(null); setSelectedChapter(data.chapters.find(c => c.id === chId) ?? null) }}
            onUpdateAct={store.updateAct}
            onUpdateChapter={store.updateChapter}
            onAddChapter={store.addChapter}
            onMoveChapter={store.moveChapter}
          />
        )
      }

      if (activeChapter) {
        return (
          <ChapterDetail
            key={activeChapter.id}
            chapter={activeChapter}
            acts={data.acts}
            projectId={projectId}
            content={data.chapterContents[activeChapter.id] ?? ''}
            contentLoaded={Object.prototype.hasOwnProperty.call(data.chapterContents, activeChapter.id)}
            onContentLoad={async (chapterId) => {
              const text = await loadChapterContent(projectId, chapterId)
              store.setChapterContent(chapterId, text)
            }}
            onUpdateChapter={store.updateChapter}
            onContentSave={async (chapterId, text) => {
              await saveChapterContent(projectId, chapterId, text)
              store.setChapterContent(chapterId, text)
            }}
            onEditGoal={(chapter) => setDetailMode({ kind: 'chapter-field', chapterId: chapter.id, field: 'goal' })}
            onEditResult={(chapter) => setDetailMode({ kind: 'chapter-field', chapterId: chapter.id, field: 'result' })}
            onEditBody={(chapter) => handleOpenBodyEditor(chapter)}
            onOpenAiPanel={(view, id) => handleOpenAiPanel(view, id)}
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
      if (ch.goal) parts.push(`要写什么：${ch.goal}\n\n`)
      if (ch.result) parts.push(`写成了什么：${ch.result}\n\n`)
      const body = data.chapterContents[ch.id]
      if (body) parts.push(body + '\n\n')
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
  const fieldEditor = detailMode?.kind === 'chapter-field' ? detailMode : null
  const fieldEditorChapter = fieldEditor
    ? data.chapters.find(c => c.id === fieldEditor.chapterId) ?? null
    : null
  const fieldEditorField: ChapterTextField | null = fieldEditor ? fieldEditor.field : null
  const previewMode = detailMode?.kind === 'preview'
  const settingsMode = detailMode?.kind === 'settings'
  const setupsMode = detailMode?.kind === 'setups'
  // 预览与导出共用同一个过滤：定稿模式只保留 status === 'final' 的章节。
  const orderedChapters = orderChapters(data.chapters, data.acts)
  const previewChapters = finalOnly
    ? orderedChapters.filter(ch => ch.status === 'final')
    : orderedChapters

  // 详情面板的内容：全屏字段编辑器 > 预览 / 伏笔 / 全局设定 > 选中项的详情。
  // renderDetail() 在"什么都没选"时返回 null——那时给一句提示，而不是一片空白。
  const selectionDetail = renderDetail()

  const detailPanel = fieldEditorChapter && fieldEditorField ? (
    <ChapterTextEditor
      key={`${fieldEditorChapter.id}:${fieldEditorField}`}
      chapter={fieldEditorChapter}
      field={fieldEditorField}
      content={fieldEditorField === 'body' ? data.chapterContents[fieldEditorChapter.id] : undefined}
      contentLoaded={Object.prototype.hasOwnProperty.call(data.chapterContents, fieldEditorChapter.id)}
      onSave={handleFieldSave}
      onClose={() => setDetailMode(null)}
    />
  ) : previewMode ? (
    <PreviewPanel
      chapters={previewChapters}
      acts={data.acts}
      contents={data.chapterContents}
      finalOnly={finalOnly}
      onFinalOnlyChange={setFinalOnly}
    />
  ) : setupsMode ? (
    <SetupPanel
      setups={data.setups}
      chapters={data.chapters}
      acts={data.acts}
      onCreate={store.addSetup}
      onUpdate={store.updateSetup}
      onDelete={store.deleteSetup}
    />
  ) : settingsMode ? (
    <GlobalSettingsPanel
      projectId={projectId}
      initialGlobalSettings={data.globalSettings}
      onSaved={(patch) => store.setData(prev => prev ? { ...prev, globalSettings: patch.global_settings } : prev)}
      onClose={() => setDetailMode(null)}
    />
  ) : selectionDetail ?? (
    <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-xs text-gray-400">详情页显示当前选中的东西</p>
      <p className="text-[11px] leading-relaxed text-gray-600">
        在幕布或大纲里点一幕、一章、一个人物；伏笔线也可以直接点。
      </p>
    </div>
  )

  const detailLabel = previewMode ? '预览'
    : settingsMode ? '全局设定'
    : setupsMode ? '伏笔'
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
          contents={data.chapterContents}
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
              onAddAct={() => store.addAct()}
              onAddChapter={() => selectedActId && store.addChapter(selectedActId)}
              onDeleteSelected={() => {
                const sel = store.selection
                if (sel.id && sel.type === 'act') setConfirmDelete({ type: 'act', id: sel.id })
                if (sel.id && sel.type === 'chapter') setConfirmDelete({ type: 'chapter', id: sel.id })
              }}
              onLayout={handleAutoLayout}
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
      {/* Icon-only left side nav: views, content management, save status, outline, setups, settings */}
      <SideNav
        activeViewId={views.activeViewId}
        onSwitchView={handleSwitchView}
        onPreview={openPreview}
        onExport={handleExport}
        finalOnly={finalOnly}
        onGlobalSetting={openGlobalSettings}
        onSetups={openSetups}
        setupsOpen={setupsMode}
        onDetail={handleDetailNav}
        detailOpen={detailOpen && detailMode === null}
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
        title={confirmDelete?.type === 'act' ? '删除幕' : '删除章'}
        message={
          confirmDelete?.type === 'act'
            ? "确定要删除「" + (data.acts.find(a => a.id === confirmDelete.id)?.name ?? '') + "」吗？该幕下的所有章节将一并删除。"
            : "确定要删除「" + (data.chapters.find(c => c.id === confirmDelete?.id)?.title ?? '') + "」吗？"
        }
        onConfirm={() => {
          if (!confirmDelete) return
          if (confirmDelete.type === 'act') { store.deleteAct(confirmDelete.id); setSelectedActId(null); setSelectedChapter(null) }
          else { store.deleteChapter(confirmDelete.id); setSelectedChapter(null) }
          setConfirmDelete(null)
        }}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  )
}
