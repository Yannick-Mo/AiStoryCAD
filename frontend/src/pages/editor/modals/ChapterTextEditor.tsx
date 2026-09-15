import { useState, useEffect, useRef, useCallback } from 'react'
import { useToast } from '../components/Toast'
import { countWords } from '../data/wordCount'
import type { Chapter } from '../types'

/** 章上可以放大来写/改的三个文本字段。 */
export type ChapterTextField = 'goal' | 'result' | 'body'

interface FieldMeta {
  /** 面板标题里的图标 */
  icon: string
  /** 顶栏标题 */
  title: string
  /** 标题右侧的那一行：这一栏在写作循环里是什么 */
  subtitle: string
  /** 标题下方的一句说明 */
  hint: string
  placeholder: string
  /** 保存成功后的提示 */
  toast: string
}

const FIELD_META: Record<ChapterTextField, FieldMeta> = {
  goal: {
    icon: '📝',
    title: '本章要写什么',
    subtitle: '动笔前的计划',
    hint: '写一段话概括本章要完成什么',
    placeholder: '动笔前的计划：这一章要推进什么、章末跟章初有什么不同、留什么钩子...',
    toast: '章节目标已保存',
  },
  result: {
    icon: '🏁',
    title: '本章写成了什么',
    subtitle: '下一章的前情',
    hint: '写完之后的事实：下一章靠它接上',
    placeholder: '写完之后的事实：结束时局面变成什么样、谁欠着谁、什么没交代完...',
    toast: '本章结果已保存',
  },
  body: {
    icon: '✍️',
    title: '正文',
    subtitle: '这一章的小说正文',
    hint: '直接改正文，保存后写回这一章',
    placeholder: '在这里写这一章的正文...',
    toast: '正文已保存',
  },
}

const STATUS_LABEL: Record<Chapter['status'], string> = {
  draft: '草稿',
  revising: '修改',
  final: '定稿',
}

const STATUS_CLASS: Record<Chapter['status'], string> = {
  draft: 'bg-gray-800 text-gray-400',
  revising: 'bg-amber-900/30 text-amber-400',
  final: 'bg-green-900/30 text-green-400',
}

interface ChapterTextEditorProps {
  chapter: Chapter
  field: ChapterTextField
  /** field === 'body' 时的正文当前内容（其余字段从 chapter 上取）。 */
  content?: string
  /** 正文是否已经取回来。没取回来时正文一个字都不能编——否则一保存就是清空。 */
  contentLoaded?: boolean
  onSave: (text: string) => Promise<void> | void
  onClose: () => void
}

/**
 * 章字段的全屏编辑器。它填满宿主容器（详情面板的正文区），**不是模态框**：
 * 保存或取消只是把详情面板还给选中的那一章。
 *
 * 「本章写成了什么」和「正文」要的是同一件东西——一段可以放大来写、带字数和
 * 保存/取消的文本，所以这里按字段泛化成一个组件，而不是三个几乎相同的文件。
 */
export default function ChapterTextEditor({
  chapter, field, content, contentLoaded = true, onSave, onClose,
}: ChapterTextEditorProps) {
  const meta = FIELD_META[field]
  const initial = field === 'body'
    ? (content ?? '')
    : (field === 'goal' ? chapter.goal ?? '' : chapter.result ?? '')
  const blocked = field === 'body' && !contentLoaded

  const { addToast } = useToast()
  const [text, setText] = useState(initial)
  const [saving, setSaving] = useState(false)
  const savedRef = useRef(initial)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const textRef = useRef(text)
  textRef.current = text

  useEffect(() => {
    textareaRef.current?.focus()
  }, [])

  const dirty = text !== savedRef.current

  const handleClose = useCallback(() => {
    if (textRef.current !== savedRef.current) {
      if (!window.confirm('有未保存的修改，确定关闭吗？')) return
    }
    onCloseRef.current()
  }, [])

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [handleClose])

  const handleSave = async () => {
    if (blocked) return
    setSaving(true)
    try {
      await onSave(text)
      savedRef.current = text
      addToast(meta.toast, 'success')
      onCloseRef.current()
    } catch {
      addToast('保存失败，请重试', 'error')
      setSaving(false)
    }
  }

  const charCount = field === 'body' ? countWords(text) : text.length

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <div className="truncate text-[11px] text-gray-500">{chapter.title}</div>
          <h4 className="truncate text-sm font-medium text-gray-100">{meta.icon} {meta.title}</h4>
        </div>
        <span className="shrink-0 rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">{charCount} 字</span>
      </div>

      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/60 bg-gray-900/50 px-4 py-1.5 text-[11px] text-gray-500">
        <span className={`rounded px-1.5 py-0.5 text-[10px] ${STATUS_CLASS[chapter.status]}`}>{STATUS_LABEL[chapter.status]}</span>
        <span className="text-gray-600">{meta.subtitle}</span>
        <span className="italic text-gray-600">{meta.hint}</span>
      </div>

      {/* writing surface: a centred measure of prose instead of an input box */}
      <div className="min-h-0 flex-1 overflow-y-auto bg-gray-950/40">
        <div className="mx-auto flex min-h-full w-full max-w-[42rem] flex-col px-6 py-6">
          <textarea
            ref={textareaRef}
            value={blocked ? '' : text}
            onChange={e => setText(e.target.value)}
            disabled={blocked}
            spellCheck={false}
            className="w-full flex-1 resize-none border-0 bg-transparent text-[15px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600 disabled:cursor-not-allowed disabled:text-gray-600"
            placeholder={blocked ? '正文还没取回来，稍等一下再打开这一栏...' : meta.placeholder}
          />
        </div>
      </div>

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
        <span className="text-[11px] text-gray-600">
          {blocked
            ? <span className="text-orange-400/80">正文尚未载入，先不要编辑</span>
            : dirty
              ? <span className="flex items-center gap-1 text-amber-500/80"><span className="h-1.5 w-1.5 rounded-full bg-amber-500/80" />未保存</span>
              : '已保存'}
        </span>
        <div className="flex shrink-0 gap-2">
          <button
            onClick={handleClose}
            className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-1.5 text-xs text-gray-300 transition-colors hover:bg-gray-700"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            disabled={saving || blocked}
            className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-amber-500 disabled:opacity-50"
          >
            {saving ? '保存中...' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
