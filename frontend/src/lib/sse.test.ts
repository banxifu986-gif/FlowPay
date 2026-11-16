import { describe, expect, it } from 'vitest'
import { parseFrame, readSse } from './sse'

describe('SSE 解析', () => {
  it('ignores heartbeats and keeps id/event/data', () => {
    expect(parseFrame(': heartbeat')).toBeNull()
    expect(parseFrame('id: 7\nevent: trace\ndata: {"seq":7}')).toEqual({
      id: '7',
      event: 'trace',
      data: '{"seq":7}',
    })
  })

  it('joins multi-line data per protocol', () => {
    expect(parseFrame('event: note\ndata: first\ndata: second')?.data).toBe('first\nsecond')
  })

  it('splits frames across chunks', async () => {
    const encoded = (value: string) => new TextEncoder().encode(value)
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded('event: trace\ndata: {"seq":1}\n'))
        controller.enqueue(encoded('\nevent: trace\ndata: {"seq":2}\n\n'))
        controller.close()
      },
    })
    const messages = []
    for await (const message of readSse(body)) messages.push(message)
    expect(messages.map((message) => message.data)).toEqual(['{"seq":1}', '{"seq":2}'])
  })
})
