import { Component } from 'react'
import { useParams } from 'react-router-dom'
import EditorShell from './layout/EditorShell'
import { ToastProvider } from './components/Toast'

class EditorErrorBoundary extends Component<{ children: React.ReactNode }, { error: Error | null }> {
  constructor(props: { children: React.ReactNode }) {
    super(props)
    this.state = { error: null }
  }
  static getDerivedStateFromError(error: Error) {
    return { error }
  }
  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center gap-4 text-sm">
          <div className="text-red-400 font-bold">编辑器加载失败</div>
          <pre className="text-gray-400 max-w-2xl whitespace-pre-wrap text-xs bg-gray-900 p-4 rounded-xl border border-gray-800">{this.state.error.message}</pre>
          <button onClick={() => { this.setState({ error: null }); window.location.reload() }} className="px-4 py-2 bg-blue-600 text-white rounded-xl text-sm mt-2">刷新重试</button>
        </div>
      )
    }
    return this.props.children
  }
}

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>()
  if (!id) return <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-500">项目 ID 无效</div>
  return (
    <EditorErrorBoundary>
      <ToastProvider>
        <EditorShell projectId={id} />
      </ToastProvider>
    </EditorErrorBoundary>
  )
}
