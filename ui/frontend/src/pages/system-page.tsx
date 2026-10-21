import * as React from "react"
import { CircleIcon, PlayIcon, SquareIcon, RefreshCwIcon } from "lucide-react"
import {
  Line,
  LineChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type Metrics = {
  cpu: number
  ram_pct: number
  ram_used: number
  ram_total: number
  disk_pct: number
  gpu_pct: number
  gpu_temp: number
  gpu_power: number
  vram_used: number
  vram_total: number
  vram_pct: number
  net_sent: number
  net_recv: number
  cpu_temp: number
  cpu_power: number
}

type MetricsPoint = Metrics & { t: string }

type Status = {
  ollama: boolean
  mode: string
  mode_label: string
  hostname: string
  time: string
}

type Budget = {
  spent?: number
  cap?: number
  [key: string]: unknown
}

type AuditEntry = {
  ts?: string
  agent?: string
  user?: string
  error?: string
  [key: string]: unknown
}

// The audit log no longer stores message text (privacy fix S-01): a redacted entry shows what happened instead.
function auditLabel(e: AuditEntry): string {
  const user = typeof e.user === "string" && !e.user.startsWith("<redacted") ? e.user : ""
  return user || String(e.skill ?? e.action ?? e.tool ?? "")
}

type ProcessInfo = {
  name: string
  description: string
  status: "running" | "stopped" | "unknown"
  pid: number | null
}

const MAX_POINTS = 40

function pct(v: number | undefined) {
  if (v === undefined || v < 0) return "N/A"
  return `${v}%`
}

export function SystemPage() {
  const [history, setHistory] = React.useState<MetricsPoint[]>([])
  const [status, setStatus] = React.useState<Status | null>(null)
  const [budget, setBudget] = React.useState<Budget | null>(null)
  const [audit, setAudit] = React.useState<AuditEntry[]>([])
  const [processes, setProcesses] = React.useState<ProcessInfo[]>([])
  const [processBusy, setProcessBusy] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  // Live metrics polling — cleanup is critical here to avoid leaking intervals
  // across mount/unmount when navigating away and back to this page.
  React.useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const res = await fetch("/api/metrics")
        const data: Metrics = await res.json()
        if (cancelled) return
        setHistory((prev) => {
          const next = [
            ...prev,
            { ...data, t: new Date().toLocaleTimeString([], { hour12: false }) },
          ]
          return next.length > MAX_POINTS ? next.slice(next.length - MAX_POINTS) : next
        })
        setError(null)
      } catch {
        if (!cancelled) setError("Cannot reach /api/metrics")
      }
    }

    poll()
    const id = setInterval(poll, 3000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  const loadStatus = React.useCallback(async () => {
    try {
      const res = await fetch("/api/status")
      setStatus(await res.json())
    } catch {
      setStatus(null)
    }
  }, [])

  const loadBudget = React.useCallback(async () => {
    try {
      const res = await fetch("/api/budget")
      setBudget(await res.json())
    } catch {
      setBudget(null)
    }
  }, [])

  const loadAudit = React.useCallback(async () => {
    try {
      const res = await fetch("/api/audit?n=30")
      const data = await res.json()
      setAudit((data.entries ?? []).slice().reverse())
    } catch {
      setAudit([])
    }
  }, [])

  const loadProcesses = React.useCallback(async () => {
    try {
      const res = await fetch("/api/processes")
      setProcesses(await res.json())
    } catch {
      setProcesses([])
    }
  }, [])

  React.useEffect(() => {
    loadStatus()
    loadBudget()
    loadAudit()
    loadProcesses()
  }, [loadStatus, loadBudget, loadAudit, loadProcesses])

  async function toggleProcess(name: string, running: boolean) {
    setProcessBusy(name)
    try {
      await fetch(`/api/processes/${name}/${running ? "stop" : "start"}`, {
        method: "POST",
      })
      await loadProcesses()
    } finally {
      setProcessBusy(null)
    }
  }

  const latest = history[history.length - 1]

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">System</h1>
        {status && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <CircleIcon
              className={cn(
                "size-2.5",
                status.ollama ? "fill-green-500 text-green-500" : "fill-destructive text-destructive",
              )}
            />
            Ollama {status.ollama ? "online" : "offline"}
            <Badge variant="outline">{status.hostname}</Badge>
            <Badge variant="outline">Mode {status.mode} — {status.mode_label}</Badge>
          </div>
        )}
      </div>

      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="CPU" value={pct(latest?.cpu)} sub={latest?.cpu_temp && latest.cpu_temp > 0 ? `${latest.cpu_temp}°C` : undefined} />
        <StatCard label="RAM" value={pct(latest?.ram_pct)} sub={latest ? `${latest.ram_used}/${latest.ram_total} GB` : undefined} />
        <StatCard label="GPU" value={pct(latest?.gpu_pct)} sub={latest?.gpu_temp && latest.gpu_temp > 0 ? `${latest.gpu_temp}°C` : undefined} />
        <StatCard label="Disk" value={pct(latest?.disk_pct)} />
        <StatCard label="VRAM" value={pct(latest?.vram_pct)} sub={latest && latest.vram_used >= 0 ? `${latest.vram_used}/${latest.vram_total} GB` : undefined} />
        <StatCard label="Net Sent" value={latest ? `${latest.net_sent} MB` : "—"} />
        <StatCard label="Net Recv" value={latest ? `${latest.net_recv} MB` : "—"} />
        <StatCard
          label="API Budget"
          value={budget?.cap !== undefined ? `$${((budget.cap ?? 0) - (budget.spent ?? 0)).toFixed(2)}` : "—"}
          sub={budget?.cap !== undefined ? `of $${budget.cap} cap` : "budget.json not loaded"}
        />
      </div>

      {/* Live chart */}
      <Card>
        <CardHeader>
          <CardTitle>Live Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={history} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                <XAxis dataKey="t" tick={{ fontSize: 10 }} minTickGap={30} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} width={32} />
                <Tooltip
                  contentStyle={{ fontSize: 12, borderRadius: 8 }}
                  labelStyle={{ fontSize: 11 }}
                />
                <Line type="monotone" dataKey="cpu" name="CPU %" stroke="var(--chart-1, #6366f1)" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line type="monotone" dataKey="ram_pct" name="RAM %" stroke="var(--chart-2, #22c55e)" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line type="monotone" dataKey="gpu_pct" name="GPU %" stroke="var(--chart-3, #eab308)" dot={false} strokeWidth={2} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Processes */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>David Processes</CardTitle>
            <Button variant="ghost" size="icon" onClick={loadProcesses}>
              <RefreshCwIcon className="size-4" />
            </Button>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {processes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No process registry data.</p>
            ) : (
              processes.map((p) => {
                const running = p.status === "running"
                return (
                  <div
                    key={p.name}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <CircleIcon
                          className={cn(
                            "size-2",
                            running ? "fill-green-500 text-green-500" : "fill-muted-foreground text-muted-foreground",
                          )}
                        />
                        <span className="truncate text-sm font-medium">{p.name}</span>
                        {p.pid && <span className="text-xs text-muted-foreground">PID {p.pid}</span>}
                      </div>
                      <p className="truncate text-xs text-muted-foreground">{p.description}</p>
                    </div>
                    <Button
                      size="sm"
                      variant={running ? "outline" : "default"}
                      disabled={processBusy === p.name}
                      onClick={() => toggleProcess(p.name, running)}
                    >
                      {running ? <SquareIcon className="size-3.5" /> : <PlayIcon className="size-3.5" />}
                      {running ? "Stop" : "Start"}
                    </Button>
                  </div>
                )
              })
            )}
          </CardContent>
        </Card>

        {/* Audit log */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Audit Log</CardTitle>
            <Button variant="ghost" size="icon" onClick={loadAudit}>
              <RefreshCwIcon className="size-4" />
            </Button>
          </CardHeader>
          <CardContent>
            <div className="max-h-72 overflow-y-auto">
              {audit.length === 0 ? (
                <p className="text-sm text-muted-foreground">No audit entries.</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {audit.map((e, i) => (
                    <li
                      key={i}
                      className={cn(
                        "flex items-baseline gap-2 border-b py-1 text-xs last:border-0",
                        e.error ? "text-destructive" : "text-muted-foreground",
                      )}
                    >
                      <span className="shrink-0 font-mono">{(e.ts ?? "").slice(11, 19)}</span>
                      <span className="shrink-0 font-medium">[{e.agent ?? "?"}]</span>
                      <span className="truncate">{auditLabel(e).slice(0, 80)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-1 px-4">
        <span className="text-xs tracking-wide text-muted-foreground uppercase">{label}</span>
        <span className="text-xl font-semibold">{value}</span>
        {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
      </CardContent>
    </Card>
  )
}
