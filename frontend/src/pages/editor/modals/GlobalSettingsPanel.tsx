import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { getProject, updateProject, type ProjectUpdatePayload } from '../../../api/auth'
import { useSessionState } from '../../../hooks/useSessionState'
import { useToast } from '../components/Toast'

interface GlobalSettingsPanelProps {
  projectId: string
  /** editor-data 里已有的全局设定：GET 回来之前先垫上，避免一闪而过的空框。 */
  initialGlobalSettings: string
  /** 保存成功后把 global_settings 同步回 editor store（本地，不再发第二个请求）。 */
  onSaved: (patch: { global_settings: string }) => void
  onClose: () => void
}

interface FormState {
  genre: string
  logline: string
  spine: string
  global_settings: string
  total_words: string
}

function blankForm(initialGlobalSettings: string): FormState {
  return {
    genre: '',
    logline: '',
    spine: '',
    global_settings: initialGlobalSettings,
    total_words: '',
  }
}

function fromProject(p: { genre?: string; logline?: string; spine?: string; global_settings?: string; total_words?: number }): FormState {
  return {
    genre: p.genre ?? '',
    logline: p.logline ?? '',
    spine: p.spine ?? '',
    global_settings: p.global_settings ?? '',
    total_words: p.total_words === undefined || p.total_words === null ? '' : String(p.total_words),
  }
}

/** 只比这 5 个字符串字段，用来判断"本地是否还有未保存的修改"。 */
function sameForm(a: FormState, b: FormState): boolean {
  return a.genre === b.genre
    && a.logline === b.logline
    && a.spine === b.spine
    && a.global_settings === b.global_settings
    && a.total_words === b.total_words
}

function FieldShell({ label, hint, onExpand, children }: {
  label: string
  hint: string
  /** 有多行内容时提供；点了把这一栏放大到整个详情面板。 */
  onExpand?: () => void
  children: ReactNode
}) {
  return (
    <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs font-medium text-gray-200">{label}</div>
          <div className="mt-0.5 text-[10px] leading-relaxed text-gray-500">{hint}</div>
        </div>
        {onExpand && (
          <button
            type="button"
            onClick={onExpand}
            title={`全屏编辑${label}`}
            className="shrink-0 rounded border border-gray-700 px-1.5 py-0.5 text-[9px] text-gray-400 transition-colors hover:border-amber-600/50 hover:text-amber-400"
          >⛶ 全屏</button>
        )}
      </div>
      <div className="mt-1.5">{children}</div>
    </section>
  )
}

const TEXTAREA_CLASS = 'w-full resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-xs leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none'

/** 可以单独放大编辑的多行字段（类型 / 目标字数是一行输入，不在此列）。 */
type ExpandableField = 'logline' | 'spine' | 'global_settings'

interface ExpandableFieldMeta {
  icon: string
  label: string
  hint: string
  placeholder: string
  rows: number
  /** 世界观 / 年表更多是结构化文本，放大后也用等宽字体。 */
  mono?: boolean
}

/**
 * 内联和全屏两种展示共用同一份文案，避免两边各改各的之后对不上。
 * 全屏编辑读写的仍然是这份表单草稿，保存依旧走底部那一个「保存」。
 */
const FIELD_META: Record<ExpandableField, ExpandableFieldMeta> = {
  logline: {
    icon: '💡',
    label: '一句话梗概',
    hint: '这个故事是什么、开局的钩子。只写目前确定的部分，后续可以再补。',
    placeholder: '例如：陈默回镇上办父亲后事，发现父亲的死牵着一笔没人敢提的旧债',
    rows: 2,
  },
  spine: {
    icon: '🧭',
    label: '当前已完成骨架',
    hint: '目前已经确定、已经创建出来（含还没写正文）的这一段走向。只写当前这一段，不用替全书定结局；后续可以往后追加。',
    placeholder: '例如：奔丧 → 发现账本缺页 → 姑妈改口 → 镇上人试探 → 旧债浮出',
    rows: 4,
  },
  global_settings: {
    icon: '🌍',
    label: '当前全局设定',
    hint: '目前已知的世界观、地理、势力、年表等参考信息。只写目前确定的部分，后续可以补。章蓝图和写手都会读到它。',
    placeholder: '在此记录你的世界设定...\n\n例如：\n■ 世界观：这是一个剑与魔法的世界，三大种族共存\n■ 主要大陆：艾泽拉斯（中部）、卡利姆多（西部）...\n■ 势力格局：联盟 vs 部落...\n■ 重要年表：第 1 年 - 黑暗之门开启...',
    rows: 10,
    mono: true,
  },
}

/**
 * 作品层设定。规格第 3 节总表里属于"这本书现在写到哪、目前确定了什么"的字段都在这儿：
 * 类型 / 一句话梗概 / **当前已完成骨架** / **当前全局设定** / 目标字数。
 *
 * 这里只记录**目前已经确定**的部分，不要求一次写完整本书：
 * 骨架包含已经创建（含还没写正文）的章，续写时再往后追加。
 *
 * 保存走 ``PATCH /api/projects/{pid}``（唯一的作品层写路径），不再经过编辑器增量同步。
 */
export default function GlobalSettingsPanel({
  projectId, initialGlobalSettings, onSaved, onClose,
}: GlobalSettingsPanelProps) {
  const { addToast } = useToast()
  // session state: 浮动 / 停靠会重挂载这个面板，草稿不能因此丢掉
  const [form, setForm] = useSessionState<FormState>(`settings.form.${projectId}`, blankForm(initialGlobalSettings))
  const [saved, setSaved] = useSessionState<FormState>(`settings.saved.${projectId}`, blankForm(initialGlobalSettings))
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  // 放大编辑的是哪一栏。跟着草稿一起放进 session：浮动 / 停靠重挂载时不至于
  // 从全屏掉回列表；关闭面板时再清掉。
  const [expanded, setExpanded] = useSessionState<ExpandableField | null>(`settings.expanded.${projectId}`, null)

  const savedRef = useRef(saved)
  savedRef.current = saved

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    getProject(projectId)
      .then(p => {
        if (cancelled) return
        const fresh = fromProject(p)
        // 本地还有未保存的修改时不要把服务端的值盖回去（浮动面板会重挂载）。
        setForm(prev => (sameForm(prev, savedRef.current) ? fresh : prev))
        setSaved(fresh)
        setLoading(false)
      })
      .catch(() => {
        if (cancelled) return
        setLoadError('加载作品设定失败')
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [projectId, reloadKey, setForm, setSaved])

  const dirty = !sameForm(form, saved)

  const patch = useCallback((updates: Partial<FormState>) => {
    setForm(prev => ({ ...prev, ...updates }))
  }, [setForm])

  const patchField = useCallback((field: ExpandableField, value: string) => {
    setForm(prev => ({ ...prev, [field]: value }))
  }, [setForm])

  // 全屏里按 Esc 只退出放大，不关整个面板，也不丢草稿。
  useEffect(() => {
    if (!expanded) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        setExpanded(null)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [expanded, setExpanded])

  const handleClose = () => {
    if (dirty && !window.confirm('有未保存的修改，确定关闭吗？')) return
    // 取消 = 丢弃草稿：session 缓存里不能留下次打开时的残留
    setForm(savedRef.current)
    setExpanded(null)
    onClose()
  }

  const handleSave = async () => {
    const words = Number.parseInt(form.total_words, 10)
    const totalWords = Number.isFinite(words) && words >= 0 ? words : 0
    const payload: ProjectUpdatePayload = {
      genre: form.genre.trim(),
      logline: form.logline,
      spine: form.spine,
      global_settings: form.global_settings,
      total_words: totalWords,
    }
    setSaving(true)
    try {
      await updateProject(projectId, payload)
      const next: FormState = { ...form, genre: payload.genre!, total_words: String(totalWords) }
      setSaved(next)
      setForm(next)
      onSaved({ global_settings: next.global_settings })
      addToast('作品设定已保存', 'success')
      // 和章正文的全屏编辑一致：保存成功后回到设定总览。
      setExpanded(null)
    } catch (e) {
      addToast(`保存失败：${(e as Error)?.message || '请重试'}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  const disabled = loading || !!loadError
  const expandedMeta = expanded ? FIELD_META[expanded] : null

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium text-gray-100">
            {expandedMeta ? `${expandedMeta.icon} ${expandedMeta.label}` : '🌐 作品设定'}
          </h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">
            {expandedMeta ? '全屏编辑 · Esc 返回设定总览' : '现在写到哪、目前确定了什么'}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {expanded && (
            <span className="rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">
              {form[expanded].length} 字
            </span>
          )}
          <span className="rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">
            {loading ? '加载中…' : dirty ? '未保存' : '已保存'}
          </span>
        </div>
      </div>

      {loadError ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="text-xs text-red-400">{loadError}</div>
          <div className="text-[11px] leading-relaxed text-gray-600">
            没有读到服务端的当前骨架 / 全局设定，保存会把它们覆盖成空——所以这里先禁用保存。
          </div>
          <button
            onClick={() => setReloadKey(k => k + 1)}
            className="rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-200 transition-colors hover:bg-gray-700"
          >重试</button>
        </div>
      ) : expanded && expandedMeta ? (
        <div className="flex min-h-0 flex-1 flex-col bg-gray-950/40">
          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-gray-800/60 bg-gray-900/40 px-4 py-1.5 text-[11px] text-gray-500">
            <span className="italic">{expandedMeta.hint}</span>
            <button
              onClick={() => setExpanded(null)}
              className="ml-auto shrink-0 rounded border border-gray-700 px-1.5 py-0.5 text-[9px] text-gray-400 transition-colors hover:border-amber-600/50 hover:text-amber-400"
              title="退出全屏，回到作品设定"
            >⛶ 退出全屏</button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto flex min-h-full w-full max-w-[56rem] flex-col px-6 py-6">
              <textarea
                key={expanded}
                autoFocus
                value={form[expanded]}
                onChange={e => patchField(expanded, e.target.value)}
                disabled={disabled}
                spellCheck={!expandedMeta.mono}
                placeholder={expandedMeta.placeholder}
                className={`w-full flex-1 resize-none border-0 bg-transparent text-[14px] leading-[1.95] tracking-[0.01em] text-gray-100 caret-amber-400 outline-none selection:bg-amber-500/25 placeholder:text-gray-600 disabled:cursor-not-allowed disabled:text-gray-600 ${expandedMeta.mono ? 'font-mono' : ''}`}
              />
            </div>
          </div>
        </div>
      ) : (
        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
          <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs font-medium text-gray-200">类型</div>
                <div className="mt-0.5 text-[10px] text-gray-500">题材</div>
                <input
                  value={form.genre}
                  onChange={e => patch({ genre: e.target.value })}
                  disabled={disabled}
                  placeholder="例如：悬疑"
                  className="mt-1.5 w-full rounded-lg border border-gray-700 bg-gray-950 px-2 py-1.5 text-xs text-gray-300 focus:border-amber-600 focus:outline-none disabled:opacity-50"
                />
              </div>
              <div>
                <div className="text-xs font-medium text-gray-200">目标字数</div>
                <div className="mt-0.5 text-[10px] text-gray-500">预计写多长（首页进度条的分母）</div>
                <input
                  type="number"
                  min={0}
                  value={form.total_words}
                  onChange={e => patch({ total_words: e.target.value })}
                  disabled={disabled}
                  placeholder="例如：300000"
                  className="mt-1.5 w-full rounded-lg border border-gray-700 bg-gray-950 px-2 py-1.5 text-xs tabular-nums text-gray-300 focus:border-amber-600 focus:outline-none disabled:opacity-50"
                />
              </div>
            </div>
          </section>

          {(['logline', 'spine', 'global_settings'] as const).map(field => {
            const meta = FIELD_META[field]
            return (
              <FieldShell
                key={field}
                label={meta.label}
                hint={meta.hint}
                onExpand={() => setExpanded(field)}
              >
                <textarea
                  value={form[field]}
                  onChange={e => patchField(field, e.target.value)}
                  disabled={disabled}
                  spellCheck={!meta.mono}
                  rows={meta.rows}
                  placeholder={meta.placeholder}
                  className={TEXTAREA_CLASS + (meta.mono ? ' font-mono' : '') + ' disabled:opacity-50'}
                />
              </FieldShell>
            )
          })}
        </div>
      )}

      <div className="flex shrink-0 items-center justify-between gap-3 border-t border-gray-800/80 bg-gray-900/70 px-4 py-2">
        <span className="text-[11px] text-gray-600">
          {dirty
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
            disabled={saving || disabled || !dirty}
            className="rounded-lg bg-amber-600 px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-amber-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
