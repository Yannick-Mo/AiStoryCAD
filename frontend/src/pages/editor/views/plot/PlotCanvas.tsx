import { useMemo, useCallback, useEffect, useState, useRef } from 'react'
import ReactFlow, {
  Background, Controls, MiniMap,
  type Node, type Edge, type NodeTypes, type ReactFlowInstance,
  useNodesState, useEdgesState, MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import ChapterNode from './ChapterNode'
import ActGroupNode from './ActGroupNode'
import type { Chapter, Act, Setup, SelectionState } from '../../types'
import { chapterNumbers, orderChapters } from '../../data/orderUtils'
import { countWords } from '../../data/wordCount'
import ContextMenu from './ContextMenu'
import { useViewportMemory } from '../../../../hooks/useViewportMemory'

const nodeTypes: NodeTypes = { chapter: ChapterNode, actGroup: ActGroupNode }
const NODE_H = 90
const ACT_H = 220
const ACT_GAP = 32
const CH_PER_ROW = 5
const ROW_GAP = 24
// 章节链的连接点固定「右出 → 左进」（handle id 见 ChapterNode.tsx）。这条链是
// 从左往右读的，所以不按几何位置挑边：换行 / 换幕时下一章在左边，B 点选边会让线
// 从章节的左边出去，读起来像倒着走。
const CHAIN_FROM = 's-r'
const CHAIN_TO = 't-l'
// 伏笔线的连接点固定在**节点顶部**：从埋设章的顶点画到收束章的顶点，两条线不会
// 踩着章节链的左右两侧走。伏笔可以往前跨几十章，顶部进出也让线从章的上面绕过去。
const SETUP_FROM = 's-t'
const SETUP_TO = 't-t'
// 伏笔线的边 id 前缀：点它要能找回 setups 里的那一行（章节链不是可选对象）。
const SETUP_EDGE_PREFIX = 'setup:'

// Act group interaction layering:
// React Flow renders edge SVGs below node DOM. If an act group DOM element covers
// the whole group area with pointer events enabled, edge clicks inside the group
// are swallowed by the group node. Therefore act group node styles keep
// pointerEvents: 'none' and blank-area group dragging/resizing is implemented by
// coordinate hit-testing on the pane capture handlers below. Keep edge/node/handle
// targets excluded in isInteractiveFlowTarget().
function getActGroupAtPosition(nodes: Node[], position: { x: number; y: number }) {
  return [...nodes].reverse().find(n => {
    if (n.type !== 'actGroup') return false
    const w = (n.style?.width as number) || 300
    const h = (n.style?.height as number) || 200
    return position.x >= n.position.x && position.x <= n.position.x + w &&
           position.y >= n.position.y && position.y <= n.position.y + h
  })
}

// Bottom-right resize zone is intentionally coordinate-based instead of DOM-based.
// A pointer-enabled resize DOM handle would sit in the node layer and can block
// edge selection near the group corner.
function isActGroupResizePosition(node: Node, position: { x: number; y: number }) {
  const w = (node.style?.width as number) || 300
  const h = (node.style?.height as number) || 200
  return position.x >= node.position.x + w - 18 && position.y >= node.position.y + h - 18
}

interface PlotCanvasProps {
  chapters: Chapter[]
  acts: Act[]
  /** 伏笔：一对配对引用，画成 planted_in → pay_off_in 的一条线。 */
  setups: Setup[]
  /** 章的正文（可能还没加载完），只用于节点上的字数。 */
  contents: Record<string, string>
  /** 点章节节点：只选中它（详情页自己要它时才显示） */
  onChapterClick?: (chapterId: string) => void
  /** 右键「编辑本章」：选中它并打开详情页——显式动作，不是自动弹 */
  onEditChapter?: (chapterId: string) => void
  onActClick?: (actId: string) => void
  onAddChapter?: (actId: string) => Chapter
  onDeleteChapter?: (chapterId: string) => void
  onAddAct?: (name?: string) => Act
  onDeleteAct?: (actId: string) => void
  onActResize?: (actId: string, width: number, height: number) => void
  selection: SelectionState
  /** 当前选中的伏笔（点那条虚线选中它） */
  selectedSetupId?: string | null
  onSelectNode: (type: 'act' | 'chapter', id: string) => void
  onSelectSetup?: (setupId: string) => void
  onClearSelection: () => void
  resetKey?: number
}

/**
 * 情节幕布只画两样东西（规格第 10 节"前端"）：
 *   ① 所有章节按 sort_order 串成一条链——纯渲染，数据层没有"时序线"这张表；
 *   ② 伏笔线——每条 setups 记录一条 planted_in → pay_off_in 的线（只在 pay_off_in 有值时画）。
 *
 * 边的创建 / 编辑 / 重连 / 类型切换全部删除：没有 edge_type 可存，也没有
 * chapter_edges 表可写。
 */
export default function PlotCanvas({
  chapters, acts, setups, contents,
  onChapterClick, onEditChapter, onActClick,
  onAddChapter, onDeleteChapter, onAddAct, onDeleteAct, onActResize,
  selection, selectedSetupId, onSelectNode, onSelectSetup, onClearSelection,
  resetKey = 0,
}: PlotCanvasProps) {
  const sortedActs = useMemo(() => [...acts].sort((a, b) => a.order - b.order), [acts])

  // 顺序来自 sort_order（幕序 → 幕内序号）。
  const orderMap = useMemo(() => chapterNumbers(chapters, acts), [chapters, acts])

  const initialNodes: Node[] = useMemo(() => {
    const result: Node[] = []
    let y = 20
    sortedActs.forEach(act => {
      const chs = chapters
        .filter(c => c.actId === act.id)
        .sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0))
      const count = chs.length
      const cols = Math.min(count, CH_PER_ROW)
      const rows = Math.max(1, Math.ceil(count / CH_PER_ROW))
      const calcW = Math.max(cols * 240 + 80, 300)
      const calcH = Math.max(ACT_H, 60 + rows * NODE_H + (rows - 1) * ROW_GAP + 20)
      const w = act.width ? Math.max(act.width, calcW) : calcW
      const h = act.height ? Math.max(act.height, calcH) : calcH
      const actNodeId = `act-${act.id}`
      result.push({
        id: actNodeId,
        type: 'actGroup',
        position: { x: 20, y },
        data: { label: act.name, color: act.color },
        style: { width: w, height: h, pointerEvents: 'none' },
        dragHandle: '.act-drag-handle',
        selectable: false,
      })
      chs.forEach((ch, i) => {
        const row = Math.floor(i / CH_PER_ROW)
        const col = i % CH_PER_ROW
        result.push({
          id: ch.id,
          type: 'chapter',
          parentId: actNodeId,
          extent: 'parent',
          position: { x: col * 240 + 40, y: 60 + row * (NODE_H + ROW_GAP) },
          data: {
            actId: ch.actId,
            actColor: act.color,
            title: ch.title,
            goal: ch.goal,
            wordCount: countWords(contents[ch.id]),
            status: ch.status,
            orderBadge: orderMap.get(ch.id),
          },
        })
      })
      y += h + ACT_GAP
    })
    return result
  }, [chapters, sortedActs, orderMap, contents])

  const rfEdges: Edge[] = useMemo(() => {
    const nodeById = new Map(initialNodes.map(n => [n.id, n]))

    // ① 章节链：所有章按叙事顺序串成一条。这是 sort_order 的渲染，不是存储。
    const chain: Edge[] = []
    const ordered = orderChapters(chapters, acts)
    for (let i = 0; i + 1 < ordered.length; i++) {
      const a = ordered[i]
      const b = ordered[i + 1]
      if (!nodeById.has(a.id) || !nodeById.has(b.id)) continue
      chain.push({
        id: `chain:${a.id}:${b.id}`,
        source: a.id,
        target: b.id,
        sourceHandle: CHAIN_FROM,
        targetHandle: CHAIN_TO,
        type: 'default',
        animated: true,
        style: { stroke: '#d4a373', strokeWidth: 3 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#d4a373' },
      })
    }

    // ② 伏笔线：每条 setups 记录一条（pay_off_in 为空表示还没定在哪收，不画）。
    // 这条线是**可以点的**：点它选中的是 setups 里的一行，详情面板显示那条伏笔。
    const setupLines: Edge[] = setups
      .filter(s => Boolean(s.payOffIn))
      .map(s => {
        const targetId = s.payOffIn as string
        if (!nodeById.has(s.plantedIn) || !nodeById.has(targetId)) return null
        const isSelected = selectedSetupId === s.id
        const stroke = isSelected ? '#fbbf24'
          : s.status === 'paid' ? '#22c55e'
          : s.status === 'dropped' ? '#6b7280'
          : '#a78bfa'
        const label = s.note.length > 20 ? s.note.slice(0, 20) + '…' : s.note
        return {
          id: `${SETUP_EDGE_PREFIX}${s.id}`,
          source: s.plantedIn,
          target: targetId,
          sourceHandle: SETUP_FROM,
          targetHandle: SETUP_TO,
          type: 'default',
          selected: isSelected,
          animated: isSelected,
          style: {
            stroke,
            strokeWidth: isSelected ? 3 : 2,
            cursor: 'pointer',
            ...(isSelected ? {} : { strokeDasharray: '6 3' }),
          },
          markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
          label,
          labelStyle: {
            fontSize: 10,
            fill: isSelected ? '#fbbf24' : '#9ca3af',
            background: '#1f2937',
            padding: '2px 6px',
            borderRadius: 4,
          },
        } as Edge
      })
      .filter((e): e is Edge => e !== null)

    return [...chain, ...setupLines]
  }, [chapters, acts, setups, initialNodes, selectedSetupId])

  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [rfEdgesState, setRfEdges, onEdgesChange] = useEdgesState([])
  const [paneCursor, setPaneCursor] = useState<'default' | 'se-resize'>('default')
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; items: { label: string; icon?: string; disabled?: boolean; onClick: () => void }[][] } | null>(null)
  const rfRef = useRef<ReactFlowInstance | null>(null)
  const viewport = useViewportMemory('plot')

  // Sync nodes from data without resetting drag positions or manual group size
  // When resetKey > 0, skip position preservation to force auto-layout
  useEffect(() => {
    setNodes(prev => {
      const prevMap = new Map(prev.map(n => [n.id, n]))
      return initialNodes.map(n => {
        const prevNode = prevMap.get(n.id)
        if (n.type === 'actGroup' && prevNode) {
          const ns = n.style || {}
          const ps = prevNode.style || {}
          return {
            ...n,
            position: resetKey ? n.position : (prevNode.position ?? n.position),
            style: {
              ...ns,
              width: Math.max((ns.width as number) || 300, (ps.width as number) || 300),
              height: Math.max((ns.height as number) || 220, (ps.height as number) || 220),
            },
          }
        }
        return { ...n, position: resetKey ? n.position : (prevNode?.position ?? n.position) }
      })
    })
  }, [initialNodes, setNodes, resetKey])
  // Sync data edges
  useEffect(() => { setRfEdges(rfEdges) }, [rfEdges, setRfEdges])

  // Sync selection state on nodes without resetting positions
  useEffect(() => {
    setNodes(prev => prev.map(n => {
      if (n.type === 'chapter') return { ...n, selected: selection.type === 'chapter' && selection.id === n.id }
      if (n.type === 'actGroup') return { ...n, selected: selection.type === 'act' && selection.id === n.id.replace('act-', '') }
      return n
    }))
  }, [selection, setNodes])

  const handleResize = useCallback((id: string, w: number, h: number) => {
    setNodes(nds => nds.map(n => n.id === id ? { ...n, style: { ...n.style, width: w, height: h } } : n))
  }, [setNodes])

  const handleResizeEnd = useCallback((id: string, w: number, h: number) => {
    setNodes(nds => nds.map(n => n.id === id ? { ...n, style: { ...n.style, width: w, height: h } } : n))
    onActResize?.(id.replace('act-', ''), w, h)
  }, [setNodes, onActResize])

  useEffect(() => {
    setNodes(nds => nds.map(n =>
      n.type === 'actGroup' ? { ...n, data: { ...n.data, onResize: handleResize, onResizeEnd: handleResizeEnd } } : n
    ))
  }, [handleResize, handleResizeEnd, setNodes])

  /** 点伏笔线：选中的是 setups 里的一行（章节链只是 sort_order 的渲染，不选）。 */
  const handleEdgeClick = useCallback((_event: React.MouseEvent, edge: Edge) => {
    if (!edge.id.startsWith(SETUP_EDGE_PREFIX)) return
    onSelectSetup?.(edge.id.slice(SETUP_EDGE_PREFIX.length))
  }, [onSelectSetup])

  const handleNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    if (node.type === 'chapter') {
      onSelectNode('chapter', node.id)
      onChapterClick?.(node.id)
    }
    if (node.type === 'actGroup') {
      const actId = node.id.replace('act-', '')
      onSelectNode('act', actId)
      onActClick?.(actId)
    }
  }, [onSelectNode, onChapterClick, onActClick])

  const isInteractiveFlowTarget = useCallback((target: EventTarget | null) => {
    if (!(target instanceof Element)) return false
    return Boolean(
      target.closest('.react-flow__edge') ||
      target.closest('.react-flow__node') ||
      target.closest('.react-flow__handle') ||
      target.closest('.react-flow__edgeupdater') ||
      target.closest('.nodrag') ||
      target.closest('[data-interactive="true"]') ||
      target.closest('button, input, textarea, select, a')
    )
  }, [])

  // Capture before React Flow pane panning. Only blank space inside an act group
  // is intercepted; edges, chapters, handles, resize-disabled DOM, and controls
  // are explicitly allowed through so their native React Flow handlers still run.
  const handleActGroupPanePointerDown = useCallback((event: React.PointerEvent) => {
    if (event.button !== 0 || isInteractiveFlowTarget(event.target)) return

    const rf = rfRef.current
    if (!rf) return

    // Close context menu before preventDefault (which suppresses mousedown)
    setCtxMenu(null)

    const startFlowPos = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const currentNodes = rf.getNodes()
    const actNode = getActGroupAtPosition(currentNodes, startFlowPos)
    if (!actNode) return

    event.preventDefault()
    event.stopPropagation()
    event.nativeEvent.stopImmediatePropagation()

    const actId = actNode.id.replace('act-', '')
    onSelectNode('act', actId)
    onActClick?.(actId)

    const pointerId = event.pointerId
    const startPosition = { ...actNode.position }
    const startWidth = (actNode.style?.width as number) || 300
    const startHeight = (actNode.style?.height as number) || 200
    const isResize = isActGroupResizePosition(actNode, startFlowPos)

    const onMove = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return
      const nextFlowPos = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY })

      if (isResize) {
        setNodes(nds => nds.map(n => n.id === actNode.id ? {
          ...n,
          style: {
            ...n.style,
            width: Math.max(300, startWidth + nextFlowPos.x - startFlowPos.x),
            height: Math.max(150, startHeight + nextFlowPos.y - startFlowPos.y),
          },
        } : n))
        return
      }

      setNodes(nds => nds.map(n => n.id === actNode.id ? {
        ...n,
        position: {
          x: startPosition.x + nextFlowPos.x - startFlowPos.x,
          y: startPosition.y + nextFlowPos.y - startFlowPos.y,
        },
      } : n))
    }

    const cleanup = (ev: PointerEvent) => {
      if (ev.pointerId !== pointerId) return
      if (isResize) {
        const finalFlowPos = rf.screenToFlowPosition({ x: ev.clientX, y: ev.clientY })
        onActResize?.(actId, Math.max(300, startWidth + finalFlowPos.x - startFlowPos.x), Math.max(150, startHeight + finalFlowPos.y - startFlowPos.y))
      }
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', cleanup)
      window.removeEventListener('pointercancel', cleanup)
    }

    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', cleanup)
    window.addEventListener('pointercancel', cleanup)
  }, [isInteractiveFlowTarget, onActClick, onSelectNode, onActResize, setNodes, setCtxMenu])

  const handleActGroupPanePointerMove = useCallback((event: React.PointerEvent) => {
    if (isInteractiveFlowTarget(event.target)) {
      setPaneCursor('default')
      return
    }

    const rf = rfRef.current
    if (!rf) {
      setPaneCursor('default')
      return
    }

    const flowPos = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const actNode = getActGroupAtPosition(rf.getNodes(), flowPos)
    setPaneCursor(actNode && isActGroupResizePosition(actNode, flowPos) ? 'se-resize' : 'default')
  }, [isInteractiveFlowTarget])

  const handlePaneClick = useCallback((event: React.MouseEvent) => {
    const rf = rfRef.current
    if (!rf) return
    const flowPos = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const currentNodes = rf.getNodes()
    const actNode = currentNodes.find(n => {
      if (n.type !== 'actGroup') return false
      const w = (n.style?.width as number) || 300
      const h = (n.style?.height as number) || 200
      return flowPos.x >= n.position.x && flowPos.x <= n.position.x + w &&
             flowPos.y >= n.position.y && flowPos.y <= n.position.y + h
    })
    if (actNode) {
      const actId = actNode.id.replace('act-', '')
      onSelectNode('act', actId)
      onActClick?.(actId)
    } else {
      onClearSelection()
    }
  }, [onClearSelection, onSelectNode, onActClick])

  // Context menus: only the two real objects, acts and chapters.
  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: Node) => {
    event.preventDefault()
    const id = node.type === 'actGroup' ? node.id.replace('act-', '') : node.id
    if (node.type === 'actGroup') {
      onSelectNode('act', id)
      setCtxMenu({
        x: event.clientX, y: event.clientY,
        items: [
          [{ label: '新建章', icon: '+', onClick: () => onAddChapter?.(id) }],
          [{ label: '删除幕', icon: '✕', onClick: () => onDeleteAct?.(id) }],
        ],
      })
    } else if (node.type === 'chapter') {
      onSelectNode('chapter', id)
      setCtxMenu({
        x: event.clientX, y: event.clientY,
        items: [
          [{ label: '编辑本章', icon: '✎', onClick: () => onEditChapter?.(id) }],
          [{ label: '删除章', icon: '✕', onClick: () => onDeleteChapter?.(id) }],
        ],
      })
    }
  }, [onSelectNode, onAddChapter, onDeleteAct, onEditChapter, onDeleteChapter])

  const onPaneContextMenu = useCallback((event: React.MouseEvent) => {
    event.preventDefault()
    const rf = rfRef.current
    if (!rf) return
    const flowPos = rf.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    const currentNodes = rf.getNodes()
    const actNode = currentNodes.find(n => {
      if (n.type !== 'actGroup') return false
      const w = (n.style?.width as number) || 300
      const h = (n.style?.height as number) || 200
      return flowPos.x >= n.position.x && flowPos.x <= n.position.x + w &&
             flowPos.y >= n.position.y && flowPos.y <= n.position.y + h
    })
    if (actNode) {
      const actId = actNode.id.replace('act-', '')
      onSelectNode('act', actId)
      setCtxMenu({
        x: event.clientX, y: event.clientY,
        items: [
          [{ label: '新建章', icon: '+', onClick: () => onAddChapter?.(actId) }],
          [{ label: '删除幕', icon: '✕', onClick: () => onDeleteAct?.(actId) }],
        ],
      })
    } else {
      onClearSelection()
      setCtxMenu({
        x: event.clientX, y: event.clientY,
        items: [
          [{ label: '新建幕', icon: '+', onClick: () => onAddAct?.() }],
        ],
      })
    }
  }, [onClearSelection, onAddAct, onSelectNode, onAddChapter, onDeleteAct])

  return (
    <div
      className="h-full w-full"
      style={{ cursor: paneCursor }}
      onPointerDownCapture={handleActGroupPanePointerDown}
      onPointerMove={handleActGroupPanePointerMove}
      onPointerLeave={() => setPaneCursor('default')}
    >
      <ReactFlow
        className={paneCursor === 'se-resize' ? 'resize-cursor' : undefined}
        nodes={nodes}
        edges={rfEdgesState}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onPaneClick={handlePaneClick}
        onNodeContextMenu={onNodeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        nodeTypes={nodeTypes}
        nodesConnectable={false}
        edgesFocusable={false}
        onInit={(instance) => { rfRef.current = instance }}
        deleteKeyCode={null}
        {...(viewport.initial ? { defaultViewport: viewport.initial } : { fitView: true })}
        onMoveEnd={viewport.onMoveEnd}
        minZoom={0.05}
        maxZoom={5}
        proOptions={{ hideAttribution: true }}
      >
        <style>{`
          /* React Flow's pane/viewport set their own cursor, so the wrapper cursor
             is not enough. This class is toggled from the same coordinate hit
             test used for act group resizing. */
          .resize-cursor .react-flow__pane,
          .resize-cursor .react-flow__renderer,
          .resize-cursor .react-flow__viewport {
            cursor: se-resize !important;
          }
        `}</style>
        <Background color="#333" gap={20} />
        <Controls className="!bg-gray-800 !border-gray-700 !rounded-lg" />
        <MiniMap
          nodeColor={(n) => n.type === 'actGroup' ? 'transparent' : (n.data as any)?.actColor ?? '#d4a373'}
          maskColor="rgba(0,0,0,0.7)"
          className="!bg-gray-900 !border-gray-700"
        />
      </ReactFlow>
      {ctxMenu && <ContextMenu {...ctxMenu} onClose={() => setCtxMenu(null)} />}
    </div>
  )
}
