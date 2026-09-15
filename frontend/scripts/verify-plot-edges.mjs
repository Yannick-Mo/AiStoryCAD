import { readFileSync, existsSync } from 'node:fs'
import assert from 'node:assert/strict'

// 情节幕布上的两类线，规则不同：
//   ① 章节链——固定"右出 → 左进"（这条链是从左往右读的，换行/换幕时也这么画）
//   ② 伏笔线——按几何位置挑连接点，但**可以点**：点它选中的是 setups 里的一行
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')

const plot = read('../src/pages/editor/views/plot/PlotCanvas.tsx')
const shell = read('../src/pages/editor/layout/EditorShell.tsx')

// 1. 章节链的连接点写死右出左进
assert.match(plot, /const CHAIN_FROM = 's-r'/, '章节链的出发点固定为右侧 handle')
assert.match(plot, /const CHAIN_TO = 't-l'/, '章节链的进入点固定为左侧 handle')
assert.match(plot, /sourceHandle: CHAIN_FROM,\s*\n\s*targetHandle: CHAIN_TO,/,
  '章节链必须用固定连接点，不能按几何位置挑')
assert.doesNotMatch(plot, /handleFor\(a\.id, b\.id/, '章节链不该再走 getBestHandle 那条"挑边"的路')
assert.doesNotMatch(plot, /sourceHandle: handles\.sourceHandle,\s*\n\s*targetHandle: handles\.targetHandle,\s*\n\s*type: 'default',\s*\n\s*animated: true/,
  '章节链不该再用挑出来的 handle')

// 2. 伏笔线固定在节点顶部进出：不踩章节链的左右两侧
assert.match(plot, /const SETUP_FROM = 's-t'/, '伏笔线的出发点固定为顶部')
assert.match(plot, /const SETUP_TO = 't-t'/, '伏笔线的进入点固定为顶部')
assert.match(plot, /sourceHandle: SETUP_FROM,\s*\n\s*targetHandle: SETUP_TO,/,
  '伏笔线要用顶部进出的固定连接点')
assert.doesNotMatch(plot, /handleFor|getBestHandle/,
  '情节幕布上两条线都写死了连接点，不该再留"按几何位置挑边"的代码')

// 3. 伏笔线可以点 → 选中 setups 里的一行
assert.match(plot, /const SETUP_EDGE_PREFIX = 'setup:'/, '伏笔边要有 id 前缀，才能从边找回伏笔行')
assert.match(plot, /id: `\$\{SETUP_EDGE_PREFIX\}\$\{s\.id\}`/, '伏笔边 id 用前缀 + setup id')
assert.match(plot, /onEdgeClick=\{handleEdgeClick\}/, 'ReactFlow 要接 onEdgeClick')
assert.match(plot, /onSelectSetup\?\.\(edge\.id\.slice\(SETUP_EDGE_PREFIX\.length\)\)/,
  '点伏笔线要把 setup id 交出去')
assert.match(plot, /const isSelected = selectedSetupId === s\.id/, '被选中的伏笔线要换配色')
assert.match(plot, /selectedSetupId, onSelectNode, onSelectSetup, onClearSelection/,
  'PlotCanvas 要收下 selectedSetupId 与 onSelectSetup')

// 4. 详情面板里能显示那条伏笔
const detail = '../src/pages/editor/views/plot/SetupDetail.tsx'
assert.ok(existsSync(new URL(detail, import.meta.url)), '要有单条伏笔的详情组件')
const setupDetail = read(detail)
assert.match(setupDetail, /export default function SetupDetail/, 'SetupDetail 默认导出')
assert.match(setupDetail, /埋在哪/, '详情里能改"埋在哪一章"')
assert.match(setupDetail, /打算在哪收/, '详情里能改"打算在哪一章收"')
assert.match(shell, /import SetupDetail from '\.\.\/views\/plot\/SetupDetail'/, 'EditorShell 要用 SetupDetail')
assert.match(shell, /if \(selectedSetup\) \{/, '选中伏笔时详情面板优先显示它')
assert.match(shell, /const handleSetupSelect = useCallback/, '选中伏笔要有单独的入口')
assert.match(shell, /onSelectSetup=\{handleSetupSelect\}/, '幕布选中伏笔要接到 shell')
assert.match(shell, /selectedSetupId=\{selectedSetupId\}/, '幕布要知道当前选中哪条伏笔')

// 5. 选中态互斥：选幕/章/人物时把伏笔让开，选伏笔时把幕/章让开
assert.match(shell, /setSelectedSetupId\(null\)\s*\n\s*setSelectedActId\(actId\)/, '点幕时清掉伏笔选中')
assert.match(shell, /setSelectedSetupId\(null\)\s*\n\s*setSelectedChapter\(ch\)/, '点章时清掉伏笔选中')
assert.match(shell, /setSelectionView\('narrative-plot'\)\s*\n\s*setSelectedActId\(null\)\s*\n\s*setSelectedChapter\(null\)/,
  '选中伏笔时把幕/章让开')

console.log('plot edge checks passed')