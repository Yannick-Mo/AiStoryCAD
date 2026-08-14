import { useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { FilePlus2, FolderUp, X } from "lucide-react"
import { createProject } from "../../api/auth"
import { createFromMaterial, type ProgressEvent } from "../../api/ai"
import { useToast } from "../editor/components/Toast"

interface Props {
  open: boolean
  onClose: () => void
}

export default function CreateProjectDialog({ open, onClose }: Props) {
  const [mode, setMode] = useState<"pick" | "empty" | "material">("pick")
  const [title, setTitle] = useState("")
  const [busy, setBusy] = useState(false)
  const [materialText, setMaterialText] = useState("")
  const [aiSteps, setAiSteps] = useState<ProgressEvent[]>([])
  const [aiGenerating, setAiGenerating] = useState(false)
  const abortCreateRef = useRef<(() => void) | null>(null)
  const { addToast } = useToast()
  const navigate = useNavigate()

  useEffect(() => {
    return () => {
      abortCreateRef.current?.()
      abortCreateRef.current = null
    }
  }, [])

  if (!open) return null

  const handleClose = () => {
    abortCreateRef.current?.()
    abortCreateRef.current = null
    onClose()
  }

  async function handleCreateEmpty() {
    if (!title.trim()) return
    setBusy(true)
    try {
      const result = await createProject(title.trim())
      navigate(`/projects/${result.id}`)
    } catch {
      setBusy(false)
      addToast("创建项目失败，请重试", "error")
    }
  }

  function handleBack() {
    setMode("pick")
    setTitle("")
  }

  function handleStartMaterial() {
    setMode("material")
    setTitle("")
    setMaterialText("")
    setAiSteps([])
    setAiGenerating(false)
  }

  function handleCreateFromMaterial() {
    if (!materialText.trim() || !title.trim()) return
    setAiGenerating(true)
    setAiSteps([])
    const events: ProgressEvent[] = []
    const cancel = createFromMaterial(
      { title: title.trim(), material: materialText.trim() },
      (evt) => {
        events.push(evt)
        setAiSteps([...events])
      },
      (projectId) => {
        navigate(`/projects/${projectId}`)
      },
      (msg) => {
        addToast(msg, "error")
        setAiGenerating(false)
      },
    )
    abortCreateRef.current = cancel
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={handleClose}>
      <div
        className="bg-gray-900 rounded-2xl p-8 w-full max-w-lg shadow-2xl border border-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        {mode === "pick" && (
          <>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-100">创建新项目</h2>
              <button onClick={onClose} className="text-gray-500 hover:text-gray-300 transition-colors">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="space-y-4">
              <button
                onClick={() => setMode("empty")}
                className="w-full flex items-center gap-5 p-5 rounded-xl bg-gray-800 border border-gray-700 hover:border-blue-500/50 hover:bg-gray-800/80 transition-all text-left group"
              >
                <div className="w-12 h-12 rounded-full bg-blue-500/10 text-blue-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                  <FilePlus2 className="w-6 h-6" />
                </div>
                <div>
                  <div className="font-bold text-gray-100 mb-0.5">空项目</div>
                  <div className="text-sm text-gray-500">从空白开始，自由创作</div>
                </div>
              </button>
              <button
                onClick={handleStartMaterial}
                className="w-full flex items-center gap-5 p-5 rounded-xl bg-gray-800 border border-gray-700 hover:border-yellow-500/50 hover:bg-gray-800/80 transition-all text-left group"
              >
                <div className="w-12 h-12 rounded-full bg-yellow-500/10 text-yellow-400 flex items-center justify-center shrink-0 group-hover:scale-105 transition-transform">
                  <FolderUp className="w-6 h-6" />
                </div>
                <div className="flex-1">
                  <div className="font-bold text-gray-100 mb-0.5">从素材创建</div>
                  <div className="text-sm text-gray-500">粘贴故事创意，AI 自动搭建项目框架</div>
                </div>
              </button>
            </div>
          </>
        )}

        {mode === "empty" && (
          <>
            <div className="flex items-center gap-3 mb-6">
              <button onClick={handleBack} className="text-gray-500 hover:text-gray-300 transition-colors">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
              </button>
              <h2 className="text-xl font-bold text-gray-100">新建空项目</h2>
            </div>
            <div className="mb-6">
              <label className="block text-sm text-gray-400 mb-2">项目名称</label>
              <input
                autoFocus
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleCreateEmpty()}
                placeholder="输入项目名称..."
                className="w-full px-4 py-3 rounded-xl bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500/50 transition-colors"
              />
            </div>
            <div className="flex justify-end gap-3">
              <button
                onClick={handleBack}
                className="px-5 py-2.5 rounded-xl text-gray-400 hover:text-gray-200 transition-colors"
              >
                返回
              </button>
              <button
                onClick={handleCreateEmpty}
                disabled={!title.trim() || busy}
                className="px-6 py-2.5 rounded-xl bg-blue-600 text-white font-bold hover:bg-blue-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                {busy ? "创建中..." : "创建项目"}
              </button>
            </div>
          </>
        )}

        {mode === "material" && !aiGenerating && aiSteps.length === 0 && (
          <>
            <div className="flex items-center gap-3 mb-6">
              <button onClick={() => setMode("pick")} className="text-gray-500 hover:text-gray-300 transition-colors">
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
              </button>
              <h2 className="text-xl font-bold text-gray-100">从素材创建</h2>
            </div>
            <div className="mb-4">
              <label className="block text-sm text-gray-400 mb-2">项目名称</label>
              <input
                autoFocus
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="输入项目名称..."
                className="w-full px-4 py-3 rounded-xl bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-600 focus:outline-none focus:border-yellow-500/50 transition-colors"
              />
            </div>
            <div className="mb-6">
              <label className="block text-sm text-gray-400 mb-2">
                创作素材 <span className="text-gray-600">（输入故事创意或点子，AI 会自动扩展搭建完整项目框架）</span>
              </label>
              <textarea
                value={materialText}
                onChange={e => setMaterialText(e.target.value)}
                placeholder="例如：一个退隐杀手重出江湖（短点子，AI 会扩展）&#10;或粘贴详细素材（最多5000字，AI 严格遵循）"
                className="w-full h-40 px-4 py-3 rounded-xl bg-gray-800 border border-gray-700 text-gray-100 placeholder-gray-600 focus:outline-none focus:border-yellow-500/50 transition-colors resize-none"
                maxLength={5000}
              />
              <div className="text-right text-[10px] text-gray-600 mt-1">{materialText.length}/5000</div>
            </div>
            <div className="flex justify-end gap-3">
              <button onClick={() => setMode("pick")} className="px-5 py-2.5 rounded-xl text-gray-400 hover:text-gray-200 transition-colors">返回</button>
              <button
                onClick={handleCreateFromMaterial}
                disabled={!title.trim() || !materialText.trim()}
                className="px-6 py-2.5 rounded-xl bg-yellow-600 text-white font-bold hover:bg-yellow-500 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
              >
                AI 分析与生成
              </button>
            </div>
          </>
        )}

        {mode === "material" && (aiGenerating || aiSteps.length > 0) && (
          <>
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-xl font-bold text-gray-100">AI 正在生成项目框架...</h2>
              <button onClick={() => { abortCreateRef.current?.(); abortCreateRef.current = null; navigate("/") }} className="text-gray-500 hover:text-white text-lg">✕</button>
            </div>
            <div className="space-y-2 mb-6">
              {["analyze_material", "plan_structure", "design_characters", "build_settings", "generate_all_scenes", "generate_edges", "validate"].map((step, idx) => {
                const evt = aiSteps.find(e => e.step === step)
                const lastStep = aiSteps[aiSteps.length - 1]?.step
                const current = !evt && aiGenerating && (
                  lastStep === step || (idx === 0 && !lastStep)
                )
                const done = !!evt
                const labels: Record<string, string> = {
                  analyze_material: "分析素材",
                  plan_structure: "规划结构",
                  design_characters: "设计角色",
                  build_settings: "生成世界观",
                  generate_all_scenes: "生成场景",
                  generate_edges: "生成连线",
                  validate: "校验结果",
                }
                const plan = aiSteps.find(e => e.step === "plan_structure")
                const totalChapters = plan?.preview ? (plan.preview.match(/(\d+)章/)?.[1] ?? "") : ""
                const sceneCount = step === "generate_all_scenes" && done && evt?.preview
                  ? (parseInt(evt.preview) || 0) : 0
                return (
                  <div key={step} className={`flex items-start gap-3 p-2 rounded-lg ${(done || current) ? 'bg-gray-800/60' : ''}`}>
                    <span className={`text-sm w-5 ${done ? 'text-green-400' : current ? 'text-amber-400 animate-pulse' : 'text-gray-600'}`}>{done ? "✓" : current ? "⟳" : "○"}</span>
                    <div className="flex-1 min-w-0">
                      <div className={`text-sm ${current ? 'text-gray-200' : done ? 'text-gray-400' : 'text-gray-600'}`}>
                        {labels[step]}{step === "generate_all_scenes" && totalChapters ? ` (${totalChapters}章)` : ""}
                      </div>
                      {step === "generate_all_scenes" && done && sceneCount > 0 && (
                        <div className="text-xs text-gray-500 mt-0.5">共 {sceneCount} 个场景</div>
                      )}
                      {step !== "generate_all_scenes" && evt?.preview && (
                        <div className="text-xs text-gray-500 mt-0.5 whitespace-pre-wrap leading-relaxed">{evt.preview}</div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
            {!aiSteps.find(e => e.step === "done") && !aiSteps.find(e => e.step === "error") && (
              <div className="text-center text-sm text-gray-500 animate-pulse">
                {aiSteps.length >= 5 ? "正在写入项目数据..." : "AI 思考中..."}
              </div>
            )}
            {aiSteps.find(e => e.step === "done") && (
              <div className="text-center text-sm text-green-400">项目创建完成！正在跳转...</div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
