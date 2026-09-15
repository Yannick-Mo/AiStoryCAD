import { readFileSync, existsSync } from 'node:fs'
import assert from 'node:assert/strict'

// 详情页的入口只有两个：左导航的「详情页」，和幕布右键的「编辑本章」。
// 幕布上的选中（点章节节点 / 点人物节点 / 点幕框 / 点大纲一行）**只选中**，
// 不再自动弹出详情面板——否则在幕布上理结构时右边不停换内容。
// 全局设定 / 已完成内容 / 伏笔是导航动作，仍然自己打开这个面板。
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')

const shell = read('../src/pages/editor/layout/EditorShell.tsx')
const nav = read('../src/pages/editor/layout/SideNav.tsx')
const plot = read('../src/pages/editor/views/plot/PlotCanvas.tsx')
const chapterDetail = read('../src/pages/editor/views/plot/ChapterDetail.tsx')
const textEditor = read('../src/pages/editor/modals/ChapterTextEditor.tsx')

// 1. 没有"选中就自动开面板"的逻辑
assert.doesNotMatch(shell, /hasSelection/, 'EditorShell 不该再有 hasSelection 这条自动开面板的线')
assert.doesNotMatch(shell, /useEffect\(\(\) => \{\s*setDetailOpen\(true\)/, '详情面板不该被 useEffect 自动打开')

// 1b. 进入工作台默认就把详情面板摆出来（方便）。这是**挂载时的一次性默认值**，
// 与「选中就弹」是两件事——上面那条禁的是后者。
assert.match(shell, /const \[detailOpen, setDetailOpen\] = useState\(true\)/,
  '进入工作台时详情面板默认是打开的')

// 2. 左导航的「详情页」开关接上了
assert.match(shell, /onDetail=\{handleDetailNav\}/, '侧栏「详情页」要接 handleDetailNav')
assert.match(shell, /detailOpen=\{detailOpen && detailMode === null\}/, '「详情页」的高亮只表示"正停在详情页上"')
assert.match(nav, /label="详情页"/, 'SideNav 里要有「详情页」这一项')
assert.match(nav, /onClick=\{onDetail\}/, '「详情页」按钮要调 onDetail')

// 3. 面板自己的 ✕ 与导航项共用同一套关闭语义
assert.match(shell, /const closeDetail = useCallback/, 'closeDetail 仍然是关闭详情面板的唯一出口')
assert.match(shell, /if \(detailOpen && detailMode === null\) \{ closeDetail\(\); return \}/,
  '已经停在详情页上时再点一次要关掉')

// 4. 导航动作照旧自己打开面板
for (const [name, re] of [
  ['已完成内容', /const openPreview = useCallback\(\(\) => \{\s*setDetailOpen\(true\)/],
  ['全局设定', /const openGlobalSettings = useCallback\(\(\) => \{\s*setDetailOpen\(true\)/],
  ['伏笔', /const openSetups = useCallback\(\(\) => \{\s*setDetailOpen\(true\)/],
]) {
  assert.match(shell, re, `${name} 仍然要自己打开详情面板`)
}
assert.match(shell, /onPreview=\{openPreview\}/, '预览入口接在侧栏上')

// 5. 幕布：点节点只选中；右键「编辑本章」才打开详情页
assert.match(plot, /onEditChapter\?: \(chapterId: string\) => void/, 'PlotCanvas 要有单独的 onEditChapter')
assert.match(plot, /\{ label: '编辑本章', icon: '✎', onClick: \(\) => onEditChapter\?\.\(id\) \}/,
  '右键「编辑本章」要走 onEditChapter（显式动作），不再是节点点击那条路')
assert.match(shell, /const handleEditChapter = useCallback/, 'handleEditChapter 要选中并打开详情页')
assert.match(shell, /onChapterClick=\{handleChapterClick\}/, '章节节点点击仍然只选中')

// 6. 章详情：三栏都能全屏（计划 / 结果 / 正文）
assert.match(chapterDetail, /onEditGoal\?: \(chapter: Chapter\) => void/, '「本章要写什么」的全屏入口')
assert.match(chapterDetail, /onEditResult\?: \(chapter: Chapter\) => void/, '「本章写成了什么」的全屏入口')
assert.match(chapterDetail, /onEditBody\?: \(chapter: Chapter\) => void/, '正文的全屏入口')
assert.match(chapterDetail, /⛶ 全屏/, '正文那一栏要有全屏按钮')
assert.match(chapterDetail, /const handleOpenFullscreenBody = async/, '正文全屏前要先把未保存的草稿存掉')
for (const field of ["'goal'", "'result'", "'body'"]) {
  assert.ok(textEditor.includes(field), `全屏编辑器要支持 ${field}`)
}
assert.match(shell, /field: 'goal'/, '计划栏指向全屏编辑器的 goal')
assert.match(shell, /field: 'result'/, '结果栏指向全屏编辑器的 result')
assert.match(shell, /field: 'body'/, '正文栏指向全屏编辑器的 body')

// 7. 旧的单字段编辑器不要留下
assert.ok(existsSync(new URL('../src/pages/editor/modals/ChapterTextEditor.tsx', import.meta.url)),
  'ChapterTextEditor.tsx 应该在')
assert.doesNotMatch(shell, /ChapterGoalEditor/, 'EditorShell 不该再引用 ChapterGoalEditor')

// 8. 没选中任何东西时给提示，而不是一片空白
assert.match(shell, /详情页显示当前选中的东西/, '详情页要有空状态提示')

console.log('detail panel entry checks passed')