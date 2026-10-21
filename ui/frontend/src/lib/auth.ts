// HUD login helpers (2026-10-21, workstream 3). The server decides everything (hud_auth.py); this file only follows it.

export type AuthStatus = { login: "on" | "off" | "unconfigured"; signed_in: boolean }

export async function getAuthStatus(): Promise<AuthStatus | null> {
  try {
    const r = await fetch("/api/auth/status", { credentials: "same-origin" })
    if (!r.ok) return null
    return (await r.json()) as AuthStatus
  } catch {
    return null
  }
}

export async function signIn(password: string): Promise<{ ok: boolean; error?: string }> {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  })
  if (r.ok) return { ok: true }
  try {
    const body = (await r.json()) as { error?: string }
    return { ok: false, error: body.error ?? "Sign-in failed." }
  } catch {
    return { ok: false, error: "Sign-in failed." }
  }
}

export async function signOut(): Promise<void> {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" })
  } finally {
    window.location.assign("/login")
  }
}

// Any API call that comes back 401 (session expired, signed out elsewhere) sends the browser to the login page.
// Installed once in main.tsx; the auth endpoints themselves are left alone.
export function installAuthRedirect(): void {
  const original = window.fetch.bind(window)
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const response = await original(input, init)
    if (response.status === 401) {
      const url = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url
      const path = url.startsWith("http") ? new URL(url).pathname : url
      if (path.startsWith("/api/") && !path.startsWith("/api/auth/") && window.location.pathname !== "/login") {
        window.location.assign("/login")
      }
    }
    return response
  }
}
