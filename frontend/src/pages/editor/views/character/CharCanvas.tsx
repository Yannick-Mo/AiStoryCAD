import { useMemo, useCallback, useEffect, useState, useRef } from 'react'
import ReactFlow, {
  Background, Controls,
  type Node, type Edge, type NodeTypes, type ReactFlowInstance,
  useNodesState, useEdgesState, MarkerType,
} from 'reactflow'
import 'reactflow/dist/style.css'
import CharacterNode from './CharacterNode'
import ContextMenu from '../plot/ContextMenu'
import type { Character } from '../../types'
import { getBestHandle } from '../shared/getBestHandle'

const nodeTypes: NodeTypes = { character: CharacterNode }

interface CharCanvasProps {
  characters: Character[]
  selection: { type: 'character' | 'relation' | null; id: string | null }
  onSelectCharacter: (id: string) => void
  onSelectRelation: (sourceId: string, relationId: string) => void
  onClearSelection: () => void
  onAddCharacter: () => void
  onDeleteCharacter: (id: string) => void
  onAddRelation: (sourceId: string, targetId: string, sourceHandle: string, targetHandle: string) => void
  onDeleteRelation: (characterId: string, relationId: string) => void
}

const NODE_W = 176
const NODE_H = 72

function nodeCenter(pos: { x: number; y: number }) {
  return { x: pos.x + NODE_W / 2, y: pos.y + NODE_H / 2 }
}

export default function CharCanvas({
  characters, selection,
  onSelectCharacter, onSelectRelation, onClearSelection,
  onAddCharacter, onDeleteCharacter,
  onAddRelation, onDeleteRelation,
}: CharCanvasProps) {
  const rfRef = useRef<ReactFlowInstance | null>(null)
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; items: { label: string; icon?: string; disabled?: boolean; onClick: () => void }[][] } | null>(null)

  const selectedCharId = selection.type === 'character' ? selection.id : null

  const connectedIds = useMemo(() => {
    if (!selectedCharId) return new Set<string>()
    const ids = new Set<string>([selectedCharId])
    for (const ch of characters) {
      for (const rel of ch.relations) {
        if (ch.id === selectedCharId) ids.add(rel.targetId)
        if (rel.targetId === selectedCharId) ids.add(ch.id)
      }
    }
    return ids
  }, [characters, selectedCharId])

  const visibleNodes: Node[] = useMemo(() =>
    characters
      .filter(ch => !selectedCharId || connectedIds.has(ch.id))
      .map((ch, i) => ({
        id: ch.id,
        type: 'character',
        position: { x: 60 + (i % 3) * 300, y: 100 + Math.floor(i / 3) * 220 },
        data: { name: ch.name, role: ch.role, relations: ch.relations },
      })),
  [characters, selectedCharId, connectedIds])

  const visibleEdges: Edge[] = useMemo(() => {
    if (!selectedCharId) {
      // Normal mode: show all edges
      const nodes = visibleNodes
      const result: Edge[] = []
      const nodeMap = new Map(nodes.map(n => [n.id, n]))
      for (const ch of characters) {
        for (const rel of ch.relations) {
          const src = nodeMap.get(ch.id)
          const tgt = nodeMap.get(rel.targetId)
          if (!src || !tgt) continue
          const a = nodeCenter(src.position)
          const b = nodeCenter(tgt.position)
          const { sourceHandle, targetHandle } = getBestHandle(a, b)
          const isSelected = selection.type === 'relation' && selection.id === `${ch.id}|${rel.id}`
          result.push({
            id: `e-${ch.id}-${rel.id}`,
            source: ch.id,
            target: rel.targetId,
            sourceHandle,
            targetHandle,
            type: 'default',
            label: (rel.label || rel.type).slice(0, 20) + ((rel.label || rel.type).length > 20 ? '…' : ''),
            selected: isSelected,
            style: { stroke: isSelected ? '#60a5fa' : '#8a8a8a', strokeWidth: isSelected ? 2 : 1.5 },
            markerEnd: { type: MarkerType.ArrowClosed, color: isSelected ? '#60a5fa' : '#8a8a8a' },
          })
        }
      }
      return result
    }

    // Focus mode: only edges connected to selected character
    const nodes = visibleNodes
    const result: Edge[] = []
    const nodeMap = new Map(nodes.map(n => [n.id, n]))
    for (const ch of characters) {
      if (ch.id !== selectedCharId && !connectedIds.has(ch.id)) continue
      for (const rel of ch.relations) {
        if (ch.id !== selectedCharId && rel.targetId !== selectedCharId) continue
        const src = nodeMap.get(ch.id)
        const tgt = nodeMap.get(rel.targetId)
        if (!src || !tgt) continue
        const a = nodeCenter(src.position)
        const b = nodeCenter(tgt.position)
        const { sourceHandle, targetHandle } = getBestHandle(a, b)
        result.push({
          id: `e-${ch.id}-${rel.id}`,
          source: ch.id,
          target: rel.targetId,
          sourceHandle,
          targetHandle,
          type: 'default',
          label: (rel.label || rel.type).slice(0, 20) + ((rel.label || rel.type).length > 20 ? '…' : ''),
          selected: false,
          style: { stroke: '#fbbf24', strokeWidth: 2 },
          markerEnd: { type: MarkerType.ArrowClosed, color: '#fbbf24' },
        })
      }
    }
    return result
  }, [characters, visibleNodes, selection, selectedCharId, connectedIds])

  const [nodes, setNodes, onNodesChange] = useNodesState(visibleNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(visibleEdges)

  useEffect(() => {
    setNodes(prev => {
      const prevMap = new Map(prev.map(n => [n.id, n]))
      return visibleNodes.map(n => ({ ...n, position: prevMap.get(n.id)?.position ?? n.position }))
    })
  }, [visibleNodes, setNodes])

  useEffect(() => { setEdges(visibleEdges) }, [visibleEdges, setEdges])

  const onConnect = useCallback((conn: import('reactflow').Connection) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return
    if (!conn.sourceHandle || !conn.targetHandle) return
    onAddRelation(conn.source, conn.target, conn.sourceHandle, conn.targetHandle)
  }, [onAddRelation])

  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setCtxMenu(null)
    onSelectCharacter(node.id)
  }, [onSelectCharacter])

  const onEdgeClick = useCallback((_event: React.MouseEvent, edge: Edge) => {
    setCtxMenu(null)
    onSelectRelation(edge.source, edge.id.replace(`e-${edge.source}-`, ''))
  }, [onSelectRelation])

  const onPaneClick = useCallback(() => {
    setCtxMenu(null)
    onClearSelection()
  }, [onClearSelection])

  const onNodeContextMenu = useCallback((event: React.MouseEvent, node: Node) => {
    event.preventDefault()
    onSelectCharacter(node.id)
    setCtxMenu({
      x: event.clientX, y: event.clientY,
      items: [
        [{ label: '新建角色', icon: '+', onClick: onAddCharacter }],
        [{ label: '删除角色', icon: '✕', onClick: () => onDeleteCharacter(node.id) }],
      ],
    })
  }, [onSelectCharacter, onAddCharacter, onDeleteCharacter])

  const onEdgeContextMenu = useCallback((event: React.MouseEvent, edge: Edge) => {
    event.preventDefault()
    const relationId = edge.id.replace(`e-${edge.source}-`, '')
    onSelectRelation(edge.source, relationId)
    setCtxMenu({
      x: event.clientX, y: event.clientY,
      items: [
        [{ label: '删除连线', icon: '✕', onClick: () => onDeleteRelation(edge.source, relationId) }],
      ],
    })
  }, [onSelectRelation, onDeleteRelation])

  const onPaneContextMenu = useCallback((event: React.MouseEvent) => {
    event.preventDefault()
    setCtxMenu({
      x: event.clientX, y: event.clientY,
      items: [
        [{ label: '新建角色', icon: '+', onClick: onAddCharacter }],
      ],
    })
  }, [onAddCharacter])

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        onNodeContextMenu={onNodeContextMenu}
        onEdgeContextMenu={onEdgeContextMenu}
        onPaneContextMenu={onPaneContextMenu}
        nodeTypes={nodeTypes}
        defaultEdgeOptions={{ type: 'default' }}
        onInit={(instance) => { rfRef.current = instance }}
        deleteKeyCode="Delete"
        fitView
        minZoom={0.3}
        maxZoom={2}
        connectionRadius={48}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#333" gap={20} />
        <Controls className="!bg-gray-800 !border-gray-700 !rounded-lg" />
      </ReactFlow>
      {ctxMenu && <ContextMenu {...ctxMenu} onClose={() => setCtxMenu(null)} />}
    </div>
  )
}
