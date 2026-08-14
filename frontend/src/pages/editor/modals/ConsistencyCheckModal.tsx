import { useCallback, useEffect, useRef, useState } from 'react'
import {
  checkConsistency,
  watchConsistencyJob,
  type ConsistencyIssue,
  type ConsistencyJobProgress,
  type ConsistencyReport,
} from '../../../api/consistency'

interface Props {
  projectId: string
  onClose: () => void
  onNavigate?: (location: { entity_id?: string; chapter_id?: string; scene_id?: string }) => void
}

const STAGE_LABELS: Record<string, string> = {
  pending: '等待中',
  load: '数据加载',
  rules: '结构规则检查',
  extract: '事实提取',
  merge: '归并与别名消解',
  verify: '冲突判定',
  global: '全局审查',
  assemble: '报告生成',
  done: '完成',
}

export default function ConsistencyCheckModal({ projectId, onClose, onNavigate }: Props) {
  const [running, setRunning] = useState(false)
  const [report, setReport] = useState<ConsistencyReport | null>(null)
  const [stage, setStage] = useState('')
  const [progress, setProgress] = useState<ConsistencyJobProgress | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<string | null>(null)

  const stopRunning = useCallback(() => setRunning(false), [])

  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [])

  const handleCheck = async (force = false) => {
    setRunning(true)
    setError(null)
    setStage('pending')
    setProgress(null)
    setMessage('')
    abortRef.current?.abort()
    try {
      const result = await checkConsistency(projectId, { force })
      if ('job_id' in result && result.job_id) {
        setStage('pending')
        const controller = new AbortController()
        abortRef.current = controller
        await watchConsistencyJob(result.job_id, {
          onProgress: ({ stage, progress, message }) => {
            setStage(stage)
            setProgress(progress)
            setMessage(message)
          },
          onDone: ({ report }) => {
            setReport(report)
            stopRunning()
          },
          onError: ({ message }) => {
            setError(message || '检查失败')
            stopRunning()
          },
        }, controller.signal)
      } else {
        setReport(result as ConsistencyReport)
        stopRunning()
      }
    } catch (e) {
      if (abortRef.current?.signal.aborted) return
      setError(e instanceof Error ? e.message : '检查失败')
      stopRunning()
    }
  }

  const severityIcon = (s: string) => {
    if (s === 'error' || s === 'high' || s === 'critical') return '🔴'
    if (s === 'warning' || s === 'medium') return '🟡'
    return '🟢'
  }

  const pct = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0

  const issueLocation = (issue: ConsistencyIssue) => {
    if (issue.scene_id) return { scene_id: issue.scene_id }
    if (issue.chapter_id) return { chapter_id: issue.chapter_id }
    if (issue.entity_id) return { entity_id: issue.entity_id }
    return null
  }

  return (
    <div className="fixed inset-0 bg-gray-950/80 backdrop-blur-sm z-50 flex items-center justify-center" onClick={onClose}>
      <div className="bg-gray-900 border border-gray-700 rounded-2xl w-[560px] max-h-[80vh] overflow-y-auto shadow-2xl" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-lg font-semibold text-white">✅ 一致性检查</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl leading-none">&times;</button>
        </div>

        <div className="p-6">
          {!report && !running && !error && (
            <div className="text-center py-8">
              <p className="text-sm text-gray-400 mb-4">检查角色一致性、时间线逻辑、世界观冲突</p>
              <button
                onClick={() => handleCheck()}
                className="px-6 py-2 rounded-full text-sm bg-gradient-to-r from-amber-700/80 to-amber-600/80 border border-amber-500/50 text-white hover:from-amber-600 hover:to-amber-500 transition-all"
              >
                开始检查
              </button>
            </div>
          )}

          {running && (
            <div className="py-8">
              <div className="flex items-center justify-center gap-3 mb-4">
                <div className="inline-block w-6 h-6 border-2 border-amber-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-gray-300">{STAGE_LABELS[stage] || stage || '正在检查'}</p>
              </div>
              <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
                <div className="h-full bg-gradient-to-r from-amber-600 to-amber-400 transition-all duration-300" style={{ width: `${pct}%` }} />
              </div>
              <p className="text-xs text-gray-500 mt-2 text-center">
                {message || (progress && progress.total > 0 ? `已完成 ${progress.done}/${progress.total}` : '准备中...')}
              </p>
            </div>
          )}

          {error && (
            <div className="text-center py-6">
              <p className="text-sm text-red-400 mb-4">{error}</p>
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={() => handleCheck(true)}
                  className="px-6 py-2 rounded-full text-sm bg-gradient-to-r from-amber-700/80 to-amber-600/80 border border-amber-500/50 text-white hover:from-amber-600 hover:to-amber-500 transition-all"
                >
                  重试
                </button>
                <button onClick={onClose} className="px-4 py-2 rounded-full text-sm text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 transition-all">
                  关闭
                </button>
              </div>
            </div>
          )}

          {report && (
            <>
              <div className="flex items-start justify-between gap-2 mb-4 p-3 rounded-lg bg-gray-800/60">
                <p className="text-sm text-gray-300">{report.summary}</p>
                <button
                  onClick={() => handleCheck(true)}
                  className="text-xs text-amber-500 hover:text-amber-400 whitespace-nowrap mt-0.5"
                >
                  重新检查
                </button>
              </div>
              <div className="space-y-2">
                {report.issues.length === 0 && (
                  <p className="text-sm text-gray-400 text-center py-4">没有发现一致性问题 🎉</p>
                )}
                {report.issues.map((issue, i) => {
                  const loc = issueLocation(issue)
                  return (
                    <div key={i} className="p-3 rounded-lg bg-gray-800/40 border border-gray-700/50">
                      <div className="flex items-start gap-2">
                        <span className="text-sm mt-0.5">{severityIcon(issue.severity)}</span>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-200">
                            [{issue.check_type}] {issue.entity_type}: {issue.description}
                          </p>
                          {issue.suggestion && (
                            <p className="text-xs text-gray-400 mt-0.5">💡 {issue.suggestion}</p>
                          )}
                        </div>
                        {loc && onNavigate && (
                          <button
                            onClick={() => onNavigate(loc!)}
                            className="text-xs text-amber-500 hover:text-amber-400 whitespace-nowrap"
                          >
                            定位到
                          </button>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
