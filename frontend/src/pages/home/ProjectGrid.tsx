import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { Pencil } from "lucide-react"
import type { ProjectListItem, HomeProject } from "../../types/project"
import { COVER_GRADIENTS, PROGRESS_CLASSES } from "../../types/project"

const STAGE_MAP: Record<string, { label: string; type: "progress" | "done" }> = {
  init: { label: "结构设计中", type: "progress" },
  draft: { label: "情节填充中", type: "progress" },
  revising: { label: "校验中", type: "progress" },
  final: { label: "已完成", type: "done" },
}

/**
 * 进度 = 已写字数 / 目标字数。
 *
 * 以前这里是按 status 猜出来的（final=100 / revising=75 / draft=40 / 其他=10）——
 * 那是把阶段徽章重复一遍的假数字。written_words 是服务端对全部章正文实时算出来的，
 * target_words 是用户设的目标，两个真数相除才是这张卡上唯一有信息量的进度。
 * 阶段徽章和筛选仍然用 status，那才是它该管的事。
 *
 * target_words 为 0 / 未设置时比例无从谈起：退回 0，不编一个数。
 */
function progressPercent(p: ProjectListItem): number {
  const target = p.target_words ?? 0
  if (target <= 0) return 0
  const written = p.written_words ?? 0
  return Math.min(100, Math.max(0, Math.round((written / target) * 100)))
}

function enrichProject(p: ProjectListItem, index: number): HomeProject {
  const stage = STAGE_MAP[p.status] ?? STAGE_MAP.init
  return {
    ...p,
    coverClass: COVER_GRADIENTS[index % COVER_GRADIENTS.length],
    coverChar: p.title.charAt(0),
    stage: stage.label,
    stageType: stage.type,
    progress: progressPercent(p),
    progressClass: PROGRESS_CLASSES[index % PROGRESS_CLASSES.length],
  }
}

interface ProjectGridProps {
  projects: ProjectListItem[]
  loading: boolean
  onDeleteProject: (id: string) => void
  onRenameProject: (id: string) => void
}

export default function ProjectGrid({ projects, loading, onDeleteProject, onRenameProject }: ProjectGridProps) {
  const [activeFilter, setActiveFilter] = useState("all")
  const navigate = useNavigate()

  const enriched = projects.map((p, i) => enrichProject(p, i))

  const filtered = enriched.filter((p) => {
    if (activeFilter === "progress") return p.stageType === "progress"
    if (activeFilter === "done") return p.stageType === "done"
    if (activeFilter === "recent") {
      if (!p.updated_at) return false
      const diff = Date.now() - new Date(p.updated_at).getTime()
      return diff / (1000 * 60 * 60 * 24) <= 7
    }
    return true
  })

  const filters = [
    { key: "all", label: "全部" },
    { key: "progress", label: "进行中" },
    { key: "done", label: "已完成" },
    { key: "recent", label: "最近一周" },
  ]

  return (
    <section>
      <div className="flex items-center justify-between flex-wrap gap-3 mt-8 mb-4">
        <h2 className="text-lg font-bold text-gray-100 flex items-center gap-2">
          <span>📂</span> 继续创作
        </h2>
        <div className="flex items-center gap-2">
          <div className="flex gap-1.5">
            {filters.map((f) => (
              <button
                key={f.key}
                onClick={() => setActiveFilter(f.key)}
                className={`px-3.5 py-1.5 rounded-full text-xs font-medium border transition-all ${
                  activeFilter === f.key
                    ? "bg-blue-600 border-blue-600 text-white"
                    : "bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-600 hover:text-gray-200"
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
          <span className="text-xs text-blue-400 ml-2 opacity-50">
            全部项目
          </span>
        </div>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-500">加载中...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 bg-gray-900 rounded-2xl border-2 border-dashed border-gray-800">
          <div className="text-4xl mb-2 opacity-70">📭</div>
          <div className="text-sm text-gray-400 font-medium">没有找到匹配的项目</div>
          <div className="text-xs text-gray-500 mt-1">试试调整搜索词或筛选条件</div>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(270px,1fr))] gap-4">
          {filtered.map((proj, i) => {
            const written = proj.written_words ?? 0
            const target = proj.target_words ?? 0
            return (
              <div
                key={proj.id}
                onClick={() => navigate(`/projects/${proj.id}`)}
                className="relative bg-gray-900 border border-gray-800 rounded-2xl cursor-pointer overflow-hidden transition-all hover:shadow-xl hover:-translate-y-1 active:scale-[0.98] group"
                style={{ animation: `fadeInUp 0.5s ease ${i * 0.06}s forwards`, opacity: 0 }}
              >
                <div className={`h-24 flex items-center justify-center text-white text-3xl font-bold relative overflow-hidden ${proj.coverClass}`}>
                  {proj.coverChar}
                  <span className={`absolute top-3 right-3 text-[10px] font-semibold px-2 py-0.5 rounded-full backdrop-blur-sm ${
                    proj.stageType === "done" ? "bg-green-500/80" : "bg-blue-500/80"
                  }`}>
                    {proj.stage}
                  </span>
                </div>
                <button
                  onClick={(e) => { e.stopPropagation(); onDeleteProject(proj.id) }}
                  className="absolute top-1 left-1 w-6 h-6 flex items-center justify-center rounded-full bg-black/50 text-white/0 group-hover:text-white/70 hover:!text-red-400 text-xs transition-all opacity-0 group-hover:opacity-100"
                  title="删除项目"
                >
                  ✕
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); onRenameProject(proj.id) }}
                  className="absolute top-1 right-1 w-6 h-6 flex items-center justify-center rounded-full bg-black/50 text-white/0 group-hover:text-white/70 hover:!text-blue-400 text-xs transition-all opacity-0 group-hover:opacity-100"
                  title="重命名项目"
                >
                  <Pencil className="w-3 h-3" />
                </button>
                <div className="p-4 flex flex-col gap-1.5">
                  <div className="font-bold text-sm text-gray-100">《{proj.title}》</div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-gray-500">
                    <span>{written > 0 ? `已写 ${written.toLocaleString()} 字` : "暂无字数"}</span>
                    {target > 0 ? (
                      <>
                        <span>目标 {target.toLocaleString()} 字</span>
                        <span className="tabular-nums text-gray-400">{proj.progress}%</span>
                      </>
                    ) : (
                      <span title="在编辑器的「作品设定」里填上目标字数，这里就会显示完成度">未设目标</span>
                    )}
                    <span>{proj.total_chapters ? `${proj.total_chapters} 章` : ""}</span>
                    {proj.genre && <span>{proj.genre}</span>}
                  </div>
                  <div className="h-1 bg-gray-800 rounded-full mt-1 overflow-hidden">
                    <div className={`h-full rounded-full transition-all duration-500 ${proj.progressClass === "purple" ? "bg-purple-400" : proj.progressClass === "blue" ? "bg-blue-400" : proj.progressClass === "pink" ? "bg-pink-400" : proj.progressClass === "gold" ? "bg-yellow-400" : "bg-green-400"}`}
                      style={{ width: `${proj.progress}%` }}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
      <style>{`
        @keyframes fadeInUp { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>
    </section>
  )
}
