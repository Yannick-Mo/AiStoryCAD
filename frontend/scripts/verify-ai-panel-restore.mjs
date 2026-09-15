import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

// 刷新页面之后要"回到现场"，不只是模式：
//   ① AI 面板开着还是关着，按项目记在浏览器里；
//   ② 当前那个会话也记着，重新打开时自动载回来（会话列表是权威：已被删掉就不恢复）；
//   ③ 「新建对话」是明确动作——要把记录删掉，下次刷新不许再回到旧会话。
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')

const panel = read('../src/pages/editor/modals/AiChatPanel.tsx')
const shell = read('../src/pages/editor/layout/EditorShell.tsx')

// 1. 面板开关
assert.match(shell, /const AI_PANEL_KEY = 'aistorycad_ai_panel'/, '面板开关要有自己的存储键')
assert.match(shell, /useState\(\(\) => readStoredPanelOpen\(projectId\)\)/,
  'AI 面板的初值要来自本地存储，不能写死 false')
assert.match(shell, /storePanelOpen\(projectId, aiChatOpen\)/, '面板开关变化时要写回')
assert.match(shell, /localStorage\.getItem\(`\$\{AI_PANEL_KEY\}:\$\{projectId\}`\) === '1'/, '按项目存')

// 2. 会话 id 的记住与恢复
assert.match(panel, /const CONV_STORAGE_PREFIX = 'aistorycad_ai_conv:'/, '会话 id 要有存储键')
assert.match(panel, /function readStoredConversationId\(projectId: string\): string \| null/, '要能读回会话 id')
assert.match(panel, /function storeConversationId\(projectId: string, conversationId: string \| null\)/,
  '要能写入/清除会话 id')
assert.match(panel, /localStorage\.removeItem\(CONV_STORAGE_PREFIX \+ projectId\)/, '清除要走 removeItem')

// 恢复：先取列表，确认还在，再载入
assert.match(panel, /const last = readStoredConversationId\(projectId\)[\s\S]{0,200}if \(data\.conversations\.some\(c => c\.id === last\)\) loadConversation\(last\)/,
  '恢复会话前必须确认它还在列表里（可能已被删掉）')
assert.match(panel, /setConversations\(data\.conversations\)/, '那次请求仍然要刷新会话列表')

// 三个显式记录点
assert.match(panel, /setConversationId\(id\)\s*\n\s*storeConversationId\(projectId, id\)/, '新建会话成功后要记住')
assert.match(panel, /convIdRef\.current = convId\s*\n\s*storeConversationId\(projectId, convId\)/, '切到某个会话要记住')
assert.match(panel, /storeConversationId\(projectId, null\)/, '新建对话要清掉记录')

// 3. 恢复用的那条 effect 必须在 loadConversation 之后（否则依赖数组会撞上 TDZ）
const loadIdx = panel.indexOf('const loadConversation = useCallback')
const restoreIdx = panel.indexOf('const last = readStoredConversationId(projectId)')
assert.ok(loadIdx > 0 && restoreIdx > loadIdx,
  '恢复会话的 effect 必须写在 loadConversation 定义之后')

console.log('ai panel restore checks passed')