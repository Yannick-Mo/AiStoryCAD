import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import type { CharacterNodeData } from '../../types'

const ROLE_LABELS: Record<string, string> = {
  main: '主角',
  supporting: '配角',
  minor: '龙套',
}

function CharacterNode({ data, selected }: NodeProps<CharacterNodeData>) {
  return (
    <div className={`bg-gray-800 border-2 border-gray-700 rounded-xl px-4 py-3 shadow-lg w-44 cursor-pointer hover:-translate-y-0.5 transition-all select-none group ${selected ? 'ring-2 ring-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.3)]' : ''}`}>
      <Handle type="target" position={Position.Left} id="t-l" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="target" position={Position.Top} id="t-t" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="target" position={Position.Right} id="t-r" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="target" position={Position.Bottom} id="t-b" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="source" position={Position.Right} id="s-r" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="source" position={Position.Top} id="s-t" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="source" position={Position.Bottom} id="s-b" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />
      <Handle type="source" position={Position.Left} id="s-l" className="!bg-amber-400 !w-3 !h-3 !border-2 !border-gray-900 opacity-0 group-hover:opacity-100 transition-opacity" />

      <div className="font-semibold text-amber-100 text-sm truncate">{data.name}</div>
      <div className="text-[10px] text-gray-500 mt-0.5">{ROLE_LABELS[data.role] ?? data.role}</div>
    </div>
  )
}

export default memo(CharacterNode)
