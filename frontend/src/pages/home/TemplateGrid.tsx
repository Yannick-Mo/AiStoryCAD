import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { createProject } from "../../api/auth"
import { createEntity } from "../../api/editor"

const ACT_COUNTS: Record<string, number> = {
  '三幕式': 3,
  '四幕结构': 4,
  '英雄之旅': 3,
  '救猫咪节拍表': 3,
  '网文爽文节奏': 5,
}

const TEMPLATES = [
  { icon: "🐱", name: "救猫咪节拍表", desc: "15 个节拍，适合商业类型片与强情节小说", color: "bg-pink-500/10 text-pink-400" },
  { icon: "⚔️", name: "英雄之旅", desc: "12 阶段经典模型，适合冒险成长类故事", color: "bg-blue-500/10 text-blue-400" },
  { icon: "🏛️", name: "四幕结构", desc: "经典叙事框架，适合 10 万字以上长篇小说", color: "bg-yellow-500/10 text-yellow-400" },
  { icon: "🎬", name: "三幕式", desc: "简洁高效，适合中短篇及剧本创作", color: "bg-green-500/10 text-green-400" },
  { icon: "🔥", name: "网文爽文节奏", desc: "高密度爽点设计，适合网络连载小说", color: "bg-purple-500/10 text-purple-400" },
]

export default function TemplateGrid() {
  const [creating, setCreating] = useState(false)
  const navigate = useNavigate()

  async function handleClick(template: string) {
    if (creating) return
    setCreating(true)
    try {
      const result = await createProject(`未命名 - ${template}`)
      const actCount = ACT_COUNTS[template] ?? 3
      // 不传 sort_order：位置是服务端的事。通用 POST /{entity_type} 与编辑器增量
      // 同步两条创建路径都会先调 assign_initial_sort_order，把新幕接在末尾并编号
      // 1..N；客户端传的裸序号会被丢弃（sort_order 是服务端独占字段）。
      for (let i = 0; i < actCount; i++) {
        await createEntity(result.id, 'acts', { name: `第 ${i + 1} 幕`, color: ['#f97316', '#8b5cf6', '#06b6d4', '#ec4899', '#10b981'][i % 5] })
      }
      navigate(`/projects/${result.id}`)
    } catch {
      setCreating(false)
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between flex-wrap gap-3 mt-8 mb-4">
        <h2 className="text-lg font-bold text-gray-100 flex items-center gap-2">
          <span>📚</span> 推荐叙事模板
        </h2>
        <span className="text-xs text-blue-400 opacity-50">
          浏览全部模板
        </span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(220px,1fr))] gap-3">
        {TEMPLATES.map((t) => (
          <div
            key={t.name}
            onClick={() => handleClick(t.name)}
            className="bg-gray-900 border border-gray-800 rounded-xl p-4 cursor-pointer flex gap-3 hover:border-gray-600 hover:shadow-lg hover:-translate-y-0.5 transition-all active:scale-[0.97]"
          >
            <div className={`w-10 h-10 rounded-lg flex items-center justify-center text-lg shrink-0 ${t.color}`}>
              {t.icon}
            </div>
            <div className="min-w-0">
              <div className="font-semibold text-sm text-gray-100">{t.name}</div>
              <div className="text-xs text-gray-500 mt-0.5 leading-relaxed">{t.desc}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
