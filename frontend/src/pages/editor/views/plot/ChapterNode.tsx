import { memo } from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import type { ChapterNodeData } from '../../types'

const STATUS_LABEL: Record<string, string> = { draft: '草稿', revising: '修改', final: '定稿' }
const STATUS_DOT: Record<string, string> = { draft: 'bg-gray-500', revising: 'bg-amber-500', final: 'bg-green-500' }

/**
 * 章的画布节点。章是最底层：节点下面没有"场"了。
 *
 * Handles stay because React Flow needs anchor points to draw the chapter chain
 * and the setup lines, but `nodesConnectable={false}` on the canvas means they
 * are never drag targets — there is no stored edge layer to create into.
 */
function ChapterNode({ data, selected }: NodeProps<ChapterNodeData>) {
  const handleClass = '!w-2.5 !h-2.5 !border-2 !border-gray-900 opacity-0 !bg-amber-400'
  return (
    <div className={`relative bg-gray-800 rounded-xl px-4 py-3 shadow-lg w-44 cursor-pointer hover:-translate-y-0.5 transition-all select-none ${selected ? 'ring-2 ring-blue-500 shadow-[0_0_10px_rgba(59,130,246,0.3)]' : ''}`}
      style={{ borderTop: `3px solid ${data.actColor}` }}
    >
      <Handle type="target" position={Position.Top} id="t-t" isConnectable={false} className={handleClass} />
      <Handle type="target" position={Position.Left} id="t-l" isConnectable={false} className={handleClass} />
      <Handle type="target" position={Position.Right} id="t-r" isConnectable={false} className={handleClass} />
      <Handle type="target" position={Position.Bottom} id="t-b" isConnectable={false} className={handleClass} />
      <Handle type="source" position={Position.Top} id="s-t" isConnectable={false} className={handleClass} />
      <Handle type="source" position={Position.Left} id="s-l" isConnectable={false} className={handleClass} />
      <Handle type="source" position={Position.Bottom} id="s-b" isConnectable={false} className={handleClass} />
      <Handle type="source" position={Position.Right} id="s-r" isConnectable={false} className={handleClass} />

      {data.orderBadge && (
        <span className="absolute -top-2 -left-2 w-5 h-5 rounded-full bg-amber-600 text-[10px] font-bold text-black flex items-center justify-center shadow-md">
          {data.orderBadge}
        </span>
      )}

      <div className="flex items-center justify-between mb-1">
        <span className="text-xs text-gray-500 truncate">{data.goal}</span>
        <div className="flex items-center gap-1 shrink-0">
          <span className={`w-1.5 h-1.5 rounded-full ${STATUS_DOT[data.status]}`} />
          <span className="text-[10px] text-gray-600">{STATUS_LABEL[data.status]}</span>
        </div>
      </div>
      <div className="font-semibold text-amber-100 text-sm truncate">{data.title}</div>
      <div className="flex items-center justify-between mt-2 text-[10px] text-gray-500">
        <span>{data.wordCount > 0 ? `${data.wordCount} 字` : '未开始'}</span>
      </div>
    </div>
  )
}

export default memo(ChapterNode)
