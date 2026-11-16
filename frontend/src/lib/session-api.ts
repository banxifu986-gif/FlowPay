import { auth, notifyUnauthorized, type Identity } from '@/lib/auth'
import type { Meta, Session, TraceEvent } from '@/state'

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export type LoginResponse = {
  token: string
  expires_at: string
  username: string
  role: Identity['role']
  account_id: string | null
}

async function failure(response: Response): Promise<ApiError> {
  let detail = `请求失败 (${response.status})`
  try {
    const error = (await response.json()) as { detail?: unknown }
    if (typeof error.detail === 'string') detail = error.detail
  } catch {
    /* 非 JSON 错误正文使用 HTTP 状态提示。 */
  }
  // 401 由登录态层统一处理：清令牌并跳回登录页，调用方只管抛错。
  if (response.status === 401) notifyUnauthorized()
  return new ApiError(detail, response.status)
}

async function request<T>(
  path: string,
  method: 'GET' | 'POST' | 'DELETE',
  body?: unknown,
): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    method === 'GET'
      ? { headers: auth.headers() }
      : {
          method,
          headers: { 'Content-Type': 'application/json', ...auth.headers() },
          body: JSON.stringify(body),
        },
  )
  if (!response.ok) throw await failure(response)
  return (await response.json()) as T
}

// HTTP 路径和请求格式只在此处定义；界面调用含义明确的动作。
export const sessionApi = {
  login: (username: string, password: string) =>
    request<LoginResponse>('/auth/login', 'POST', { username, password }),
  deleteSession: (id: string) =>
    request<{ deleted_ids: string[] }>(`/sessions/${encodeURIComponent(id)}`, 'DELETE'),
  deleteSessions: (ids: string[]) =>
    request<{ deleted_ids: string[] }>('/sessions/batch-delete', 'POST', { session_ids: ids }),
  getMeta: () => request<Meta>('/meta', 'GET'),
  listSessions: () => request<Session[]>('/sessions', 'GET'),
  createSession: () => request<Session>('/sessions', 'POST', {}),
  getEvents: (id: string) => request<TraceEvent[]>(`/sessions/${id}/events`, 'GET'),
  submitMessage: (id: string, requestId: string, content: string) =>
    request(`/sessions/${id}/messages`, 'POST', { request_id: requestId, content }),
  resumeRun: (id: string, requestId: string) =>
    request(`/sessions/${id}/continue`, 'POST', { request_id: requestId }),
  cancelRun: (id: string) => request(`/sessions/${id}/cancel`, 'POST', {}),
  // SSE 走 Authorization 请求头（specs/005）：EventSource 无法带头，所以订阅改 fetch 流。
  stream: async (id: string, after: number, signal: AbortSignal): Promise<Response> => {
    const response = await fetch(`/api/sessions/${id}/stream?after=${after}`, {
      headers: auth.headers(),
      signal,
    })
    if (!response.ok) throw await failure(response)
    if (!response.body) throw new ApiError('SSE 响应缺少正文', response.status)
    return response
  },
}
export const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : String(error)
