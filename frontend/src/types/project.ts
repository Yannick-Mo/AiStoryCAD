export interface ProjectListItem {
  id: string
  title: string
  genre?: string
  status: string
  /** 目标字数（设定值，= projects.total_words）。 */
  target_words?: number
  /** 已写字数，由服务端对全部章正文实时算出来。 */
  written_words?: number
  total_chapters?: number
  created_at: string
  updated_at?: string
}

export interface HomeProject extends ProjectListItem {
  coverClass: string
  coverChar: string
  stage: string
  stageType: string
  progress: number
  progressClass: string
}

export const COVER_GRADIENTS = ["grad-purple", "grad-blue", "grad-pink", "grad-gold", "grad-green", "grad-teal"] as const
export const PROGRESS_CLASSES = ["purple", "blue", "pink", "gold", "green"] as const
