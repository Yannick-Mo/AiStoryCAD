import { readFileSync } from 'node:fs'
import assert from 'node:assert/strict'

// 「对话模式 / 协作模式」必须活过页面刷新：
//   ① 前端把选中的模式按项目记在 localStorage 里（初始化就读回来）；
//   ② 后端把每个会话的模式（agent_state.mode）跟着会话详情返回，切回旧会话时恢复；
//   ③ 两个来源都不许把模式硬编码回 "chat"。
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')

const panel = read('../src/pages/editor/modals/AiChatPanel.tsx')
const api = read('../src/api/ai_v2.ts')
const conv = read('../../backend/app/agent/memory/conversation.py')

// 1. 前端：按项目记在 localStorage，初始化时读回来
assert.match(panel, /const MODE_STORAGE_PREFIX = 'aistorycad_ai_mode:'/, '模式要有一个按项目的存储键')
assert.match(panel, /function readStoredMode\(projectId: string\): AiMode/, '要有读回模式的地方')
assert.match(panel, /function storeMode\(projectId: string, mode: AiMode\)/, '要有写回模式的地方')
assert.match(panel, /useState<AiMode>\(\(\) => readStoredMode\(projectId\)\)/,
  'mode 的初值必须来自本地存储，不能写死 chat')
assert.doesNotMatch(panel, /useState<'chat' \| 'cowriter'>\('chat'\)/,
  '不许再有无条件回落到对话模式的写法')
assert.match(panel, /const setMode = useCallback\(\(next: AiMode\) => \{\s*\n\s*setModeState\(next\)\s*\n\s*storeMode\(projectId, next\)/,
  '切模式时要同时写回本地存储')
assert.match(panel, /localStorage\.setItem\(MODE_STORAGE_PREFIX \+ projectId, mode\)/, '确实写进了 localStorage')

// 2. 切回旧会话时用那个会话自己的模式
assert.match(panel, /if \(detail\.mode === 'chat' \|\| detail\.mode === 'cowriter'\) setMode\(detail\.mode\)/,
  '载入会话时要恢复它的模式')
assert.match(api, /mode\?: 'chat' \| 'cowriter'/, '会话详情类型要带 mode')

// 3. 后端：会话详情返回 mode，并且只认两个值
assert.match(conv, /mode = \(conv\.agent_state or \{\}\)\.get\("mode"\)/, '模式取自 agent_state')
assert.match(conv, /if mode not in \("chat", "cowriter"\):\s*\n\s*mode = "chat"/,
  '非法/历史模式值一律当对话模式')
assert.match(conv, /"mode": mode,\s*\n\s*"messages": \[/, '会话详情要把 mode 一起返回')

// 4. 模式仍然照旧发给后端（别把这条线改断了）
assert.match(panel, /mode: modeRef\.current,/, '请求里仍然要带上当前模式')

console.log('ai mode persistence checks passed')