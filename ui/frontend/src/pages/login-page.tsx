import { useEffect, useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"
import { LockIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { getAuthStatus, signIn } from "@/lib/auth"

export function LoginPage() {
  const navigate = useNavigate()
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // Already signed in, or the login is switched off: there is nothing to do here.
  useEffect(() => {
    getAuthStatus().then((s) => {
      if (s && (s.login === "off" || s.signed_in)) navigate("/", { replace: true })
    })
  }, [navigate])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    if (!password || busy) return
    setBusy(true)
    setError(null)
    const result = await signIn(password)
    setBusy(false)
    if (result.ok) {
      setPassword("")
      navigate("/", { replace: true })
    } else {
      setError(result.error ?? "Sign-in failed.")
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <LockIcon className="size-4" />
            David AI
          </CardTitle>
          <CardDescription>Enter the HUD password to continue.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="flex flex-col gap-3">
            <Input
              type="password"
              name="password"
              autoComplete="current-password"
              autoFocus
              placeholder="Password"
              aria-label="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button type="submit" disabled={busy || !password}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
