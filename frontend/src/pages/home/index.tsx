import { useState, useEffect, useCallback } from "react"
import HomeNavbar from "./HomeNavbar"
import AnnouncementBanner from "./AnnouncementBanner"
import HeroSection from "./HeroSection"
import StatsRow from "./StatsRow"
import ProjectGrid from "./ProjectGrid"
import CreateCards from "./CreateCards"
import CreateProjectDialog from "./CreateProjectDialog"
import TemplateGrid from "./TemplateGrid"
import Footer from "./Footer"
import SettingsModal from "./SettingsModal"
import { listProjects, deleteProject, updateProject, getModelSettings } from "../../api/auth"
import type { ProjectListItem } from "../../types/project"
import ConfirmDialog from "../editor/components/ConfirmDialog"
import RenameDialog from "./RenameDialog"
import { useToast } from "../editor/components/Toast"

export default function ProjectListPage() {
  const [projects, setProjects] = useState<ProjectListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [renameTarget, setRenameTarget] = useState<string | null>(null)
  const { addToast } = useToast()

  const fetchProjects = useCallback(() => {
    setLoading(projects.length === 0)
    listProjects(1, 50, searchQuery)
      .then((data) => setProjects(data.items ?? data))
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [searchQuery])

  useEffect(() => { fetchProjects() }, [fetchProjects])

  // First-run onboarding: if no model is configured, open the settings modal.
  useEffect(() => {
    getModelSettings()
      .then(s => {
        if (!s.configured) {
          setSettingsOpen(true)
          addToast("首次使用：请先配置你的模型服务", "info")
        }
      })
      .catch(() => {})
  }, [])

  const handleConfirmDelete = useCallback(async () => {
    if (!deleteTarget) return
    try {
      await deleteProject(deleteTarget)
      setProjects(prev => prev.filter(p => p.id !== deleteTarget))
    } catch {
      addToast("删除失败，请重试", "error")
    } finally {
      setDeleteTarget(null)
    }
  }, [deleteTarget])

  const handleDelete = useCallback((id: string) => {
    setDeleteTarget(id)
  }, [])

  const handleRename = useCallback((id: string) => {
    setRenameTarget(id)
  }, [])

  const handleConfirmRename = useCallback(async (title: string) => {
    if (!renameTarget) return
    try {
      await updateProject(renameTarget, { title })
      setProjects(prev => prev.map(p => p.id === renameTarget ? { ...p, title } : p))
      addToast("重命名成功", "success")
    } catch {
      addToast("重命名失败", "error")
    } finally {
      setRenameTarget(null)
    }
  }, [renameTarget])

  const renameProject = projects.find(p => p.id === renameTarget)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <HomeNavbar searchQuery={searchQuery} onSearchChange={setSearchQuery} onCreateClick={() => setCreateOpen(true)} onSettingsClick={() => setSettingsOpen(true)} />
      <AnnouncementBanner />
      <div className="max-w-5xl mx-auto px-6 pb-12">
        <HeroSection />
        <CreateCards onCreateClick={() => setCreateOpen(true)} />
        <StatsRow projectCount={projects.length} />
        <ProjectGrid projects={projects} loading={loading} onDeleteProject={handleDelete} onRenameProject={handleRename} />
        <TemplateGrid />
        <Footer />
      </div>
      <CreateProjectDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      <SettingsModal open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      <RenameDialog
        open={renameTarget !== null}
        currentTitle={renameProject?.title ?? ""}
        onConfirm={handleConfirmRename}
        onCancel={() => setRenameTarget(null)}
      />
      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除项目"
        message="确定要删除该项目吗？此操作不可恢复。"
        confirmText="确认删除"
        cancelText="取消"
        onConfirm={handleConfirmDelete}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
