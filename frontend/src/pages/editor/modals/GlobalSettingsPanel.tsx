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
  ending: string
  spine: string
  global_settings: string
  total_words: string
}

function blankForm(initialGlobalSettings: string): FormState {
  return {
    genre: '',
    logline: '',
    ending: '',
    spine: '',
    global_settings: initialGlobalSettings,
    total_words: '',
  }
}

function fromProject(p: { genre?: string; logline?: string; ending?: string; spine?: string; global_settings?: string; total_words?: number }): FormState {
  return {
    genre: p.genre ?? '',
    logline: p.logline ?? '',
    ending: p.ending ?? '',
    spine: p.spine ?? '',
    global_settings: p.global_settings ?? '',
    total_words: p.total_words === undefined || p.total_words === null ? '' : String(p.total_words),
  }
}

/** 只比这 6 个字符串字段，用来判断"本地是否还有未保存的修改"。 */
function sameForm(a: FormState, b: FormState): boolean {
  return a.genre === b.genre
    && a.logline === b.logline
    && a.ending === b.ending
    && a.spine === b.spine
    && a.global_settings === b.global_settings
    && a.total_words === b.total_words
}

function FieldShell({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <section className="bg-gray-800/40 border border-gray-700/50 rounded-xl p-3">
      <div className="text-xs font-medium text-gray-200">{label}</div>
      <div className="mt-0.5 text-[10px] leading-relaxed text-gray-500">{hint}</div>
      <div className="mt-1.5">{children}</div>
    </section>
  )
}

const TEXTAREA_CLASS = 'w-full resize-none rounded-lg border border-gray-700 bg-gray-950 p-2 text-xs leading-relaxed text-gray-300 focus:border-amber-600 focus:outline-none'

/**
 * 作品层设定。规格第 3 节总表里属于"这本书是什么、要走到哪"的字段都在这儿：
 * 类型 / 一句话梗概 / **终局** / **主线骨架** / 全局设定 / 目标字数。
 *
 * 终局与主线骨架不是装饰：章蓝图答不出"结局是什么"、也答不出"我离结局还有多远"，
 * 没有这两个字段，每一章都在盲写。所以它们必须有用户能直接编辑的入口，而不是
 * 只能靠让 AI 调工具去写。
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

  const handleClose = () => {
    if (dirty && !window.confirm('有未保存的修改，确定关闭吗？')) return
    // 取消 = 丢弃草稿：session 缓存里不能留下次打开时的残留
    setForm(savedRef.current)
    onClose()
  }

  const handleSave = async () => {
    const words = Number.parseInt(form.total_words, 10)
    const totalWords = Number.isFinite(words) && words >= 0 ? words : 0
    const payload: ProjectUpdatePayload = {
      genre: form.genre.trim(),
      logline: form.logline,
      ending: form.ending,
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
    } catch (e) {
      addToast(`保存失败：${(e as Error)?.message || '请重试'}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  const disabled = loading || !!loadError

  return (
    <div className="flex h-full w-full min-h-0 flex-col">
      <div className="flex shrink-0 items-start justify-between gap-3 border-b border-gray-800/80 px-4 py-2.5">
        <div className="min-w-0">
          <h4 className="truncate text-sm font-medium text-gray-100">🌐 作品设定</h4>
          <div className="mt-0.5 truncate text-[11px] text-gray-500">这本书是什么、要走到哪</div>
        </div>
        <span className="shrink-0 rounded-full bg-gray-800/70 px-2 py-0.5 text-[10px] tabular-nums text-gray-500">
          {loading ? '加载中…' : dirty ? '未保存' : '已保存'}
        </span>
      </div>

      {loadError ? (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
          <div className="text-xs text-red-400">{loadError}</div>
          <div className="text-[11px] leading-relaxed text-gray-600">
            没有读到服务端的终局 / 主线骨架，保存会把它们覆盖成空——所以这里先禁用保存。
          </div>
          <button
            onClick={() => setReloadKey(k => k + 1)}
            className="rounded-lg bg-gray-800 px-3 py-1.5 text-xs text-gray-200 transition-colors hover:bg-gray-700"
          >重试</button>
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

          <FieldShell
            label="一句话梗概"
            hint="这个故事是什么、开局的钩子。"
          >
            <textarea
              value={form.logline}
              onChange={e => patch({ logline: e.target.value })}
              disabled={disabled}
              rows={2}
              placeholder="例如：陈默回镇上办父亲后事，发现父亲的死牵着一笔没人敢提的旧债"
              className={TEXTAREA_CLASS + ' disabled:opacity-50'}
            />
          </FieldShell>

          <FieldShell
            label="终局"
            hint="故事结束时局面变成什么样、主角变成什么样。没有它，每一章都答不出「我离结局还有多远」。"
          >
            <textarea
              value={form.ending}
              onChange={e => patch({ ending: e.target.value })}
              disabled={disabled}
              rows={4}
              placeholder="例如：陈默查出父亲是替人顶罪而死。他没有揭发真凶，把账本交给当年的受害人，自己留下接手杂货铺——他从一个「只想赶紧走」的人变成一个「选择留下」的人"
              className={TEXTAREA_CLASS + ' disabled:opacity-50'}
            />
          </FieldShell>

          <FieldShell
            label="主线骨架"
            hint="从开局到终局的因果主链。写成一串节点，让每一章知道自己落在链条的哪一段。"
          >
            <textarea
              value={form.spine}
              onChange={e => patch({ spine: e.target.value })}
              disabled={disabled}
              rows={4}
              placeholder="例如：奔丧 → 发现账本缺页 → 姑妈改口 → 镇上人试探 → 旧债浮出 → 父亲是替罪 → 真凶浮出 → 陈默留下"
              className={TEXTAREA_CLASS + ' disabled:opacity-50'}
            />
          </FieldShell>

          <FieldShell
            label="全局设定"
            hint="世界观、地理、势力、年表等任何参考信息。章蓝图和写手都会读到它。"
          >
            <textarea
              value={form.global_settings}
              onChange={e => patch({ global_settings: e.target.value })}
              disabled={disabled}
              spellCheck={false}
              rows={10}
              placeholder={'在此记录你的世界设定...\n\n例如：\n■ 世界观：这是一个剑与魔法的世界，三大种族共存\n■ 主要大陆：艾泽拉斯（中部）、卡利姆多（西部）...\n■ 势力格局：联盟 vs 部落...\n■ 重要年表：第 1 年 - 黑暗之门开启...'}
              className={TEXTAREA_CLASS + ' font-mono disabled:opacity-50'}
            />
          </FieldShell>
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
