import { useEffect, useState } from 'react'
import { errorMessage, sessionApi } from '@/lib/session-api'
import { readSse } from '@/lib/sse'
import { mergeEvents, statusOf, type TraceEvent } from '@/state'

export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'error'

const RETRY_DELAY_MS = 1000

/** 先读取历史，再按游标订阅；切换会话仅断开 SSE，不停止后端 Run。 */
export function useSessionEvents(
  sessionId: string | null,
  retry: number,
  refreshSessions: () => Promise<void>,
  reportError: (message: string) => void,
) {
  const [snapshot, setSnapshot] = useState<{ sessionId: string | null; events: TraceEvent[] }>({
    sessionId: null,
    events: [],
  })
  const [connection, setConnection] = useState<ConnectionStatus>('connecting')
  useEffect(() => {
    setSnapshot({ sessionId, events: [] })
    setConnection('connecting')
    if (!sessionId) return
    let disposed = false
    let cursor = 0
    let controller: AbortController | null = null
    let reconnect: ReturnType<typeof setTimeout> | null = null

    const accept = (event: TraceEvent) => {
      setSnapshot((previous) => ({ sessionId, events: mergeEvents(previous.events, [event]) }))
      if (event.type.startsWith('run.') || event.type === 'message.user')
        refreshSessions().catch((error) => {
          if (!disposed) reportError(errorMessage(error))
        })
    }

    // 一次连接：读到流结束或被中断就返回，由外层决定是否重连。
    const connect = async () => {
      controller = new AbortController()
      try {
        const response = await sessionApi.stream(sessionId, cursor, controller.signal)
        if (disposed) return
        setConnection('connected')
        for await (const message of readSse(response.body!)) {
          if (disposed) return
          if (message.event === 'session-deleted') {
            setSnapshot({ sessionId, events: [] })
            refreshSessions().catch((error) => reportError(errorMessage(error)))
            return
          }
          if (message.event !== 'trace') continue
          const event = JSON.parse(message.data) as TraceEvent
          cursor = Math.max(cursor, event.seq)
          accept(event)
        }
      } catch (error) {
        // 401 已由请求层通知登录态；这里只负责状态展示与重连计时。
        if (disposed || (error instanceof DOMException && error.name === 'AbortError')) return
        setConnection('error')
        reportError(errorMessage(error))
      }
      if (disposed) return
      setConnection((current) => (current === 'error' ? current : 'reconnecting'))
      reconnect = setTimeout(connect, RETRY_DELAY_MS)
    }

    sessionApi
      .getEvents(sessionId)
      .then((history) => {
        if (disposed) return
        setSnapshot({ sessionId, events: history })
        cursor = history.at(-1)?.seq ?? 0
        void connect()
      })
      .catch((error) => {
        if (!disposed) {
          setConnection('error')
          reportError(errorMessage(error))
        }
      })
    return () => {
      disposed = true
      if (reconnect) clearTimeout(reconnect)
      controller?.abort()
    }
  }, [sessionId, retry, refreshSessions, reportError])
  // effect 清理前也不让旧会话事件进入新会话的渲染。
  const events = snapshot.sessionId === sessionId ? snapshot.events : []
  return { events, connection, status: statusOf(events) }
}
