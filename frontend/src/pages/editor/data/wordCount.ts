/**
 * 字数是派生值（正文长度算出来），**不存**。
 *
 * 前后端各算一次，口径必须一致，否则同一个项目会出现两个"字数"。
 * 这里复刻后端 ``app/agent/utils.count_words``：CJK 逐字计数 + 非 CJK 按空白分词。
 */
export function countWords(text: string | null | undefined): number {
  if (!text) return 0
  const cjk = text.match(/[\u4e00-\u9fff]/g)?.length ?? 0
  const rest = text.replace(/[\u4e00-\u9fff]/g, '').trim()
  const nonCjk = rest ? rest.split(/\s+/).length : 0
  return cjk + nonCjk
}
