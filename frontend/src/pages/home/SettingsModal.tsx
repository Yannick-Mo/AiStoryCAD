import { useEffect, useState } from "react"
import { Loader2, Plug, Save, X, CheckCircle2, XCircle } from "lucide-react"
import {
  getModelSettings,
  updateModelSettings,
  testModelConnection,
  type ModelSettings,
  type TestResult,
} from "../../api/auth"
import { useToast } from "../editor/components/Toast"

interface Props {
  open: boolean
  onClose: () => void
}

export default function SettingsModal({ open, onClose }: Props) {
  const [form, setForm] = useState<ModelSettings | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<TestResult | null>(null)
  const { addToast } = useToast()

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setTestResult(null)
    getModelSettings()
      .then(setForm)
      .catch(() => addToast("加载模型配置失败", "error"))
      .finally(() => setLoading(false))
  }, [open])

  if (!open) return null

  function set<K extends keyof ModelSettings>(key: K, value: ModelSettings[K]) {
    setForm(prev => prev ? { ...prev, [key]: value } : prev)
  }

  async function handleSave() {
    if (!form) return
    setSaving(true)
    try {
      const saved = await updateModelSettings({
        main_model: form.main_model,
        main_base_url: form.main_base_url,
        main_api_key: form.main_api_key,
        middle_model: form.middle_model,
        fallback_models: form.fallback_models,
        embedding_base_url: form.embedding_base_url,
        embedding_model: form.embedding_model,
        embedding_api_key: form.embedding_api_key,
        embedding_proxy: form.embedding_proxy,
      })
      setForm(saved)
      addToast("模型配置已保存并生效", "success")
      onClose()
    } catch {
      addToast("保存失败，请重试", "error")
    } finally {
      setSaving(false)
    }
  }

  async function handleTest() {
    if (!form) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = await testModelConnection({
        base_url: form.main_base_url,
        api_key: form.main_api_key,
        model: form.main_model,
      })
      setTestResult(result)
      if (!result.ok) addToast("连接失败，请检查配置", "error")
    } catch {
      setTestResult({ ok: false, detail: "请求失败" })
    } finally {
      setTesting(false)
    }
  }

  const inputCls =
    "w-full px-4 py-2.5 rounded-xl bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-600 focus:outline-none focus:border-amber-500/50 transition-colors text-sm"

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={onClose}>
      <div
        className="bg-gray-900 rounded-2xl p-8 w-full max-w-2xl shadow-2xl border border-gray-800 max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-bold text-gray-100">模型配置</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {loading || !form ? (
          <div className="flex justify-center py-12 text-gray-500">
            <Loader2 className="w-6 h-6 animate-spin" />
          </div>
        ) : (
          <div className="space-y-6">
            <div>
              <div className="text-sm font-semibold text-amber-400 mb-3">主模型（对话 / 创作 / 分析）</div>
              <div className="grid grid-cols-1 gap-4">
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">模型名称</label>
                    <input
                      value={form.main_model}
                      onChange={e => set("main_model", e.target.value)}
                      placeholder="deepseek-v4-flash"
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-400 mb-1.5">Base URL</label>
                    <input
                      value={form.main_base_url}
                      onChange={e => set("main_base_url", e.target.value)}
                      placeholder="https://api.deepseek.com/v1"
                      className={inputCls}
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">API Key</label>
                  <input
                    type="password"
                    value={form.main_api_key}
                    onChange={e => set("main_api_key", e.target.value)}
                    placeholder="sk-..."
                    className={inputCls}
                  />
                </div>
              </div>
              <div className="flex items-center gap-3 mt-4">
                <button
                  onClick={handleTest}
                  disabled={testing}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-gray-800 border border-gray-700 text-gray-200 text-sm hover:bg-gray-700 disabled:opacity-40 transition-all"
                >
                  <Plug className="w-4 h-4" />
                  {testing ? "测试中..." : "测试连接"}
                </button>
                {testResult && (
                  <span className={`flex items-center gap-1.5 text-sm ${testResult.ok ? "text-green-400" : "text-red-400"}`}>
                    {testResult.ok ? <CheckCircle2 className="w-4 h-4" /> : <XCircle className="w-4 h-4" />}
                    {testResult.ok
                      ? `连接成功 (${testResult.latency_ms ?? "?"}ms${testResult.model ? `, 实际模型 ${testResult.model}` : ""})`
                      : `失败: ${testResult.detail ?? `HTTP ${testResult.status ?? "?"}`}`}
                  </span>
                )}
              </div>
            </div>

            <div className="border-t border-gray-800 pt-5">
              <div className="text-sm font-semibold text-gray-300 mb-3">可选配置</div>
              <div className="grid grid-cols-1 gap-4">
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">
                    中间压缩模型 <span className="text-gray-600">（用于工具结果压缩，留空使用主模型）</span>
                  </label>
                  <input
                    value={form.middle_model}
                    onChange={e => set("middle_model", e.target.value)}
                    placeholder="留空"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">
                    备用模型列表 <span className="text-gray-600">（逗号分隔；格式：模型名 或 模型名|api_key|base_url）</span>
                  </label>
                  <input
                    value={form.fallback_models.join(",")}
                    onChange={e => set("fallback_models", e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
                    placeholder="model-b, model-c|sk-xxx|https://..."
                    className={inputCls}
                  />
                </div>
              </div>
            </div>

            <div className="border-t border-gray-800 pt-5">
              <div className="text-sm font-semibold text-gray-300 mb-3">Embedding 模型（知识库检索，可选）</div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">Base URL</label>
                  <input
                    value={form.embedding_base_url}
                    onChange={e => set("embedding_base_url", e.target.value)}
                    placeholder="留空则使用主模型地址"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">模型名称</label>
                  <input
                    value={form.embedding_model}
                    onChange={e => set("embedding_model", e.target.value)}
                    placeholder="text-embedding-3-small"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">API Key</label>
                  <input
                    type="password"
                    value={form.embedding_api_key}
                    onChange={e => set("embedding_api_key", e.target.value)}
                    placeholder="留空则使用主模型 Key"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="block text-xs text-gray-400 mb-1.5">代理（可选）</label>
                  <input
                    value={form.embedding_proxy}
                    onChange={e => set("embedding_proxy", e.target.value)}
                    placeholder="http://host:port"
                    className={inputCls}
                  />
                </div>
              </div>
            </div>

            {form.effective_models && form.effective_models.length > 0 && (
              <div className="border-t border-gray-800 pt-4">
                <div className="text-xs text-gray-500 mb-2">当前生效模型：</div>
                <div className="flex flex-wrap gap-2">
                  {form.effective_models.map(m => (
                    <span key={m} className="px-2.5 py-1 rounded-full bg-gray-800 border border-gray-700 text-xs text-gray-300">
                      {m}
                    </span>
                  ))}
                </div>
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={onClose}
                className="px-5 py-2.5 rounded-xl text-gray-400 hover:text-gray-200 transition-colors text-sm"
              >
                取消
              </button>
              <button
                onClick={handleSave}
                disabled={saving || !form.main_model.trim() || !form.main_base_url.trim()}
                className="flex items-center gap-1.5 px-6 py-2.5 rounded-xl bg-amber-600 text-black font-bold hover:bg-amber-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all text-sm"
              >
                <Save className="w-4 h-4" />
                {saving ? "保存中..." : "保存并生效"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
