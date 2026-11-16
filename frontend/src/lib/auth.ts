/* 登录态的唯一所有者：令牌、身份与 401 回调都收在这里，组件不直接碰 localStorage。 */

export type Role = 'merchant' | 'internal'

export type Identity = {
  username: string
  role: Role
  accountId: string | null
}

const TOKEN_KEY = 'flowpay.token'
const IDENTITY_KEY = 'flowpay.identity'

function parseIdentity(raw: string | null): Identity | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<Identity>
    if (typeof value.username !== 'string') return null
    if (value.role !== 'merchant' && value.role !== 'internal') return null
    if (value.accountId !== null && typeof value.accountId !== 'string') return null
    return { username: value.username, role: value.role, accountId: value.accountId ?? null }
  } catch {
    return null
  }
}

export const auth = {
  token: (): string | null => localStorage.getItem(TOKEN_KEY),
  identity: (): Identity | null => parseIdentity(localStorage.getItem(IDENTITY_KEY)),
  headers: (): Record<string, string> => {
    const token = localStorage.getItem(TOKEN_KEY)
    return token ? { Authorization: `Bearer ${token}` } : {}
  },
  save: (token: string, identity: Identity): void => {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(identity))
  },
  clear: (): void => {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(IDENTITY_KEY)
  },
}

// 401 由请求层统一上报，界面只订阅一次并跳回登录页；组件之间不互相转告错误码。
let unauthorized: (() => void) | null = null

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorized = handler
}

export function notifyUnauthorized(): void {
  unauthorized?.()
}
