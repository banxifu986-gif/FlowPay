import { useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { auth, type Identity } from '@/lib/auth'
import { errorMessage, sessionApi } from '@/lib/session-api'

/** 登录页：只负责用户名/口令与身份落地，数据边界由后端强制（specs/005 US6）。 */
export function LoginPage({ onSignedIn }: { onSignedIn: (identity: Identity) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (pending || !username.trim() || !password) return
    setPending(true)
    setError('')
    try {
      const result = await sessionApi.login(username.trim(), password)
      const identity: Identity = {
        username: result.username,
        role: result.role,
        accountId: result.account_id,
      }
      auth.save(result.token, identity)
      onSignedIn(identity)
    } catch (failure) {
      setError(errorMessage(failure))
    } finally {
      setPending(false)
    }
  }

  return (
    <main className="flex min-h-dvh items-center justify-center bg-muted/60 px-4">
      <form
        onSubmit={submit}
        aria-label="登录"
        className="w-full max-w-sm space-y-4 rounded-lg border bg-background p-6 shadow-sm"
      >
        <header className="space-y-1">
          <h1 className="text-lg font-semibold">FlowPay 商户支持</h1>
          <p className="text-xs text-muted-foreground">用平台预置账号登录后查看自己账户的数据。</p>
        </header>
        <label className="block space-y-1 text-xs font-medium">
          用户名
          <Input
            name="username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </label>
        <label className="block space-y-1 text-xs font-medium">
          口令
          <Input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>
        {error && (
          <div role="alert" className="flex items-start gap-2 text-xs text-red-600">
            <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
        <Button type="submit" className="w-full" disabled={pending}>
          {pending ? '登录中…' : '登录'}
        </Button>
      </form>
    </main>
  )
}
