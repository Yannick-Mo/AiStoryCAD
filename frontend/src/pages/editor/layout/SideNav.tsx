import { useState } from 'react'
import {
  Network, Users, FolderOpen, Eye, Download, Settings, ListTree, Save, Check, LoaderCircle,
} from 'lucide-react'
import type { ComponentType } from 'react'
import { VIEWS } from '../types'

interface SideNavProps {
  activeViewId: string
  onSwitchView: (viewId: string) => void
  onPreview: () => void
  onExport: () => void
  onGlobalSetting: () => void
  onOutline: () => void
  dirty: boolean
  saving: boolean
  onSave: () => void
}

type IconComponent = ComponentType<{
  size?: number | string
  strokeWidth?: number | string
  className?: string
}>

const VIEW_ICONS: Record<string, IconComponent> = {
  'narrative-plot': Network,
  'narrative-char': Users,
}

function Tooltip({ label }: { label: string }) {
  return (
    <span className="pointer-events-none absolute left-full top-1/2 z-50 ml-2 -translate-y-1/2 whitespace-nowrap rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-[11px] text-gray-100 opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
      {label}
    </span>
  )
}

function IconButton({
  icon: Icon, label, active, onClick,
}: {
  icon: IconComponent
  label: string
  active?: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className={`group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors ${
        active
          ? 'bg-amber-500/15 text-amber-400'
          : 'text-gray-500 hover:bg-gray-800 hover:text-gray-200'
      }`}
    >
      <Icon size={18} strokeWidth={1.8} />
      <Tooltip label={label} />
    </button>
  )
}

function SaveButton({
  dirty, saving, onSave,
}: {
  dirty: boolean
  saving: boolean
  onSave: () => void
}) {
  const label = saving ? '保存中…' : dirty ? '保存修改' : '已保存'
  const Icon = saving ? LoaderCircle : dirty ? Save : Check
  return (
    <button
      onClick={onSave}
      disabled={!dirty || saving}
      aria-label={label}
      className={`group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors ${
        saving
          ? 'text-gray-500'
          : dirty
            ? 'bg-amber-500/15 text-amber-400 hover:bg-amber-500/25'
            : 'text-gray-600 cursor-default'
      }`}
    >
      <Icon size={18} strokeWidth={1.8} className={saving ? 'animate-spin' : undefined} />
      <Tooltip label={label} />
    </button>
  )
}

export default function SideNav({
  activeViewId, onSwitchView, onPreview, onExport, onGlobalSetting,
  onOutline, dirty, saving, onSave,
}: SideNavProps) {
  const [mgmtOpen, setMgmtOpen] = useState(false)

  return (
    <nav className="w-14 shrink-0 bg-gray-900/95 backdrop-blur-xl border-r border-gray-800 flex flex-col items-center gap-1.5 py-3">
      {VIEWS.map(v => (
        <IconButton
          key={v.id}
          icon={VIEW_ICONS[v.id] ?? Network}
          label={v.label}
          active={activeViewId === v.id}
          onClick={() => onSwitchView(v.id)}
        />
      ))}

      <div className="my-1 h-px w-8 bg-gray-800" />

      {/* 内容管理 */}
      <div className="relative">
        <button
          onClick={() => setMgmtOpen(!mgmtOpen)}
          aria-label="内容管理"
          className={`group relative flex h-10 w-10 items-center justify-center rounded-xl transition-colors ${
            mgmtOpen ? 'bg-gray-800 text-gray-200' : 'text-gray-500 hover:bg-gray-800 hover:text-gray-200'
          }`}
        >
          <FolderOpen size={18} strokeWidth={1.8} />
          {!mgmtOpen && <Tooltip label="内容管理" />}
        </button>
        {mgmtOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setMgmtOpen(false)} />
            <div className="absolute left-full top-1/2 z-50 ml-2 w-48 -translate-y-1/2 overflow-hidden rounded-xl border border-gray-700 bg-gray-900/95 shadow-xl backdrop-blur-sm">
              <button
                onClick={() => { setMgmtOpen(false); onPreview() }}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-200 transition-colors hover:bg-amber-600/20 hover:text-amber-400"
              >
                <Eye size={15} strokeWidth={1.8} /> 预览已完成内容
              </button>
              <button
                onClick={() => { setMgmtOpen(false); onExport() }}
                className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm text-gray-200 transition-colors hover:bg-amber-600/20 hover:text-amber-400"
              >
                <Download size={15} strokeWidth={1.8} /> 导出完整内容
              </button>
            </div>
          </>
        )}
      </div>

      {/* Bottom cluster: save status, outline, global settings */}
      <div className="mt-auto" />
      <div className="mb-1 h-px w-8 bg-gray-800" />
      <SaveButton dirty={dirty} saving={saving} onSave={onSave} />
      <IconButton icon={ListTree} label="大纲" onClick={onOutline} />
      <IconButton icon={Settings} label="全局设定" onClick={onGlobalSetting} />
    </nav>
  )
}