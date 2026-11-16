/* SSE 文本协议解析：把 fetch 流按空行切帧，供订阅 hook 使用；事件语义留在投影层。 */

export type SseMessage = { id?: string; event?: string; data: string }

/** 从响应体逐帧读取事件；流结束或调用方中断时正常退出。 */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseMessage> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) return
      buffer += decoder.decode(value, { stream: true })
      // 帧以空行分隔；心跳注释也走同一条切分逻辑，由调用方按 event 过滤。
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const message = parseFrame(frame)
        if (message) yield message
        boundary = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/** 解析单帧：注释行忽略，data 多行按协议拼接，其余字段原样保留。 */
export function parseFrame(frame: string): SseMessage | null {
  const data: string[] = []
  let event: string | undefined
  let id: string | undefined
  for (const line of frame.split('\n')) {
    if (!line || line.startsWith(':')) continue
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
    if (field === 'data') data.push(value)
    else if (field === 'event') event = value
    else if (field === 'id') id = value
  }
  if (!data.length) return null
  return { data: data.join('\n'), event, id }
}
