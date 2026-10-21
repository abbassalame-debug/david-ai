import * as React from "react"
import { Link } from "react-router-dom"
import { RefreshCwIcon, PlusIcon, TrashIcon, ArrowRightIcon, PlayIcon, Loader2Icon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

type Budget = { spent?: number; cap?: number }

type AuditEntry = { ts?: string; agent?: string; user?: string; skill?: string; action?: string; tool?: string }

// The audit log no longer stores message text (privacy fix S-01): a redacted entry shows what happened instead.
function auditLabel(e: AuditEntry): string {
  const user = typeof e.user === "string" && !e.user.startsWith("<redacted") ? e.user : ""
  return user || String(e.skill ?? e.action ?? e.tool ?? "")
}

type Task = {
  id: number
  title: string
  agent: string
  status: string
  created_at: string
  completed_at: string | null
}

const BUSINESS_AGENTS = [
  { id: "cfo", label: "CFO" },
  { id: "executive_advisor", label: "Exec Advisor" },
  { id: "venture_architect", label: "Venture Architect" },
  { id: "legal_counsel", label: "Legal Counsel" },
]

export function BusinessPage() {
  const [budget, setBudget] = React.useState<Budget | null>(null)
  const [audit, setAudit] = React.useState<AuditEntry[]>([])
  const [calendar, setCalendar] = React.useState<{ ok: boolean; formatted?: string; error?: string } | null>(null)
  const [gmail, setGmail] = React.useState<{ ok: boolean; formatted?: string; error?: string } | null>(null)
  const [briefing, setBriefing] = React.useState<{ pending: boolean; text?: string } | null>(null)
  const [calendarLoading, setCalendarLoading] = React.useState(false)
  const [gmailLoading, setGmailLoading] = React.useState(false)

  const [tasks, setTasks] = React.useState<Task[]>([])
  const [newTitle, setNewTitle] = React.useState("")
  const [newDesc, setNewDesc] = React.useState("")
  const [newAgent, setNewAgent] = React.useState("cfo")
  const [taskBusy, setTaskBusy] = React.useState(false)

  const [runningTaskId, setRunningTaskId] = React.useState<number | null>(null)
  const [runningOutput, setRunningOutput] = React.useState("")
  const [runError, setRunError] = React.useState<{ taskId: number; message: string } | null>(null)
  const taskWsRef = React.useRef<WebSocket | null>(null)

  const [importFiles, setImportFiles] = React.useState<string[] | null>(null)
  const [importLoading, setImportLoading] = React.useState(false)
  const [importError, setImportError] = React.useState<string | null>(null)

  const loadAudit = React.useCallback(async () => {
    try {
      const res = await fetch("/api/audit?n=20")
      const data = await res.json()
      setAudit((data.entries ?? []).slice().reverse())
    } catch {
      setAudit([])
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

  const loadTasks = React.useCallback(async () => {
    try {
      const res = await fetch("/api/tasks")
      const data = await res.json()
      setTasks(data.tasks ?? [])
    } catch {
      setTasks([])
    }
  }, [])

  const loadBriefing = React.useCallback(async () => {
    try {
      const res = await fetch("/api/briefing")
      setBriefing(await res.json())
    } catch {
      setBriefing(null)
    }
  }, [])

  React.useEffect(() => {
    loadAudit()
    loadBudget()
    loadTasks()
    loadBriefing()
  }, [loadAudit, loadBudget, loadTasks, loadBriefing])

  async function loadCalendar() {
    setCalendarLoading(true)
    try {
      const res = await fetch("/api/calendar/today")
      setCalendar(await res.json())
    } catch {
      setCalendar({ ok: false, error: "Network error" })
    } finally {
      setCalendarLoading(false)
    }
  }

  async function loadGmail() {
    setGmailLoading(true)
    try {
      const res = await fetch("/api/gmail/unread")
      setGmail(await res.json())
    } catch {
      setGmail({ ok: false, error: "Network error" })
    } finally {
      setGmailLoading(false)
    }
  }

  async function createTask() {
    if (!newTitle.trim() || !newDesc.trim()) return
    setTaskBusy(true)
    try {
      await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle, description: newDesc, agent: newAgent }),
      })
      setNewTitle("")
      setNewDesc("")
      await loadTasks()
    } finally {
      setTaskBusy(false)
    }
  }

  async function deleteTask(id: number) {
    await fetch(`/api/tasks/${id}`, { method: "DELETE" })
    await loadTasks()
  }

  React.useEffect(() => {
    return () => {
      taskWsRef.current?.close()
    }
  }, [])

  function runTask(id: number) {
    if (runningTaskId !== null) return
    taskWsRef.current?.close()
    setRunningTaskId(id)
    setRunningOutput("")
    setRunError(null)
    const startedId = id

    const proto = location.protocol === "https:" ? "wss://" : "ws://"
    const ws = new WebSocket(`${proto}${location.host}/ws/taskrun`)
    taskWsRef.current = ws

    ws.onopen = () => ws.send(JSON.stringify({ task_id: id }))
    ws.onmessage = (event) => {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }
      switch (data.type) {
        case "token": {
          const token = typeof data.token === "string" ? data.token : ""
          setRunningOutput((prev) => prev + token)
          break
        }
        case "task_done": {
          setRunningTaskId(null)
          loadTasks()
          ws.close()
          break
        }
        case "error": {
          const message = typeof data.error === "string" ? data.error : "Unknown error"
          setRunError({ taskId: startedId, message })
          setRunningTaskId(null)
          ws.close()
          break
        }
        default:
          break
      }
    }
    ws.onerror = () => {
      setRunError({ taskId: startedId, message: "WebSocket connection error." })
      setRunningTaskId(null)
    }
  }

  async function checkImportStatus() {
    setImportLoading(true)
    setImportError(null)
    try {
      const res = await fetch("/api/vault/Business/list")
      const data = await res.json()
      setImportFiles(data.files ?? [])
    } catch {
      setImportFiles(null)
      setImportError("Cannot reach vault")
    } finally {
      setImportLoading(false)
    }
  }

  const remaining = budget?.cap !== undefined ? (budget.cap - (budget.spent ?? 0)).toFixed(2) : null

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Business</h1>
        <div className="flex gap-2">
          {BUSINESS_AGENTS.map((a) => (
            <Button key={a.id} variant="outline" size="sm" asChild>
              <Link to={`/agents?agent=${a.id}`}>
                {a.label}
                <ArrowRightIcon className="size-3.5" />
              </Link>
            </Button>
          ))}
        </div>
      </div>

      {briefing?.pending && briefing.text && (
        <Card>
          <CardHeader>
            <CardTitle>Morning Briefing</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-foreground">{briefing.text}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Monthly Spend" value={budget?.spent !== undefined ? `$${budget.spent.toFixed(2)}` : "—"} />
        <StatCard label="API Budget Cap" value={budget?.cap !== undefined ? `$${budget.cap}` : "—"} />
        <StatCard label="Budget Remaining" value={remaining !== null ? `$${remaining}` : "—"} />
        <StatCard label="Open Tasks" value={String(tasks.filter((t) => t.status !== "done").length)} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Calendar + Gmail */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Calendar — Today</CardTitle>
            <Button size="sm" variant="outline" disabled={calendarLoading} onClick={loadCalendar}>
              <RefreshCwIcon className="size-3.5" />
              Load
            </Button>
          </CardHeader>
          <CardContent>
            {calendar === null ? (
              <p className="text-sm text-muted-foreground">Click Load to fetch today's events.</p>
            ) : calendar.ok ? (
              <p className="text-sm whitespace-pre-wrap text-foreground">
                {calendar.formatted || "No events scheduled today."}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Unavailable: {calendar.error ?? "Calendar integration not configured."}
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Gmail — Unread</CardTitle>
            <Button size="sm" variant="outline" disabled={gmailLoading} onClick={loadGmail}>
              <RefreshCwIcon className="size-3.5" />
              Load
            </Button>
          </CardHeader>
          <CardContent>
            {gmail === null ? (
              <p className="text-sm text-muted-foreground">Click Load to fetch unread email.</p>
            ) : gmail.ok ? (
              <p className="text-sm whitespace-pre-wrap text-foreground">
                {gmail.formatted || "No unread email."}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                Unavailable: {gmail.error ?? "Gmail integration not configured."}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Check Import Status</CardTitle>
          <Button size="sm" variant="outline" disabled={importLoading} onClick={checkImportStatus}>
            <RefreshCwIcon className="size-3.5" />
            Check
          </Button>
        </CardHeader>
        <CardContent>
          {importFiles === null && !importError ? (
            <p className="text-sm text-muted-foreground">Click Check to view the Business vault import status.</p>
          ) : importError ? (
            <p className="text-sm text-muted-foreground">Unavailable: {importError}</p>
          ) : (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-foreground">
                Business vault: {importFiles!.length} file{importFiles!.length === 1 ? "" : "s"}
              </p>
              {importFiles!.length > 0 && (
                <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto">
                  {importFiles!.map((f) => (
                    <li key={f} className="truncate rounded border px-2 py-1 text-xs text-muted-foreground" title={f}>
                      {f}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Tasks */}
        <Card>
          <CardHeader>
            <CardTitle>Tasks</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-col gap-2 rounded-md border p-3">
              <Input
                placeholder="Task title"
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
              />
              <Textarea
                placeholder="Description"
                rows={2}
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
              />
              <div className="flex items-center gap-2">
                <Select value={newAgent} onValueChange={setNewAgent}>
                  <SelectTrigger size="sm" className="w-auto">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BUSINESS_AGENTS.map((a) => (
                      <SelectItem key={a.id} value={a.id}>{a.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  className="ml-auto"
                  disabled={taskBusy || !newTitle.trim() || !newDesc.trim()}
                  onClick={createTask}
                >
                  <PlusIcon className="size-3.5" />
                  Add Task
                </Button>
              </div>
            </div>

            <ul className="flex max-h-80 flex-col gap-1 overflow-y-auto">
              {tasks.length === 0 ? (
                <p className="text-sm text-muted-foreground">No tasks yet.</p>
              ) : (
                tasks.map((t) => {
                  const isRunning = runningTaskId === t.id
                  return (
                    <li key={t.id} className="rounded-md border px-3 py-2">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-medium">{t.title}</span>
                            <Badge variant="outline">{t.agent}</Badge>
                            <Badge variant={t.status === "done" ? "secondary" : "default"}>
                              {isRunning ? "running" : t.status}
                            </Badge>
                          </div>
                          <p className="text-xs text-muted-foreground">{t.created_at}</p>
                        </div>
                        <div className="flex shrink-0 items-center gap-1">
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={runningTaskId !== null || t.status === "running"}
                            onClick={() => runTask(t.id)}
                            title="Run task"
                          >
                            {isRunning ? (
                              <Loader2Icon className="size-3.5 animate-spin" />
                            ) : (
                              <PlayIcon className="size-3.5" />
                            )}
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={isRunning}
                            onClick={() => deleteTask(t.id)}
                          >
                            <TrashIcon className="size-3.5" />
                          </Button>
                        </div>
                      </div>
                      {isRunning && (
                        <pre className="mt-2 max-h-40 overflow-y-auto rounded border bg-muted/40 p-2 text-xs whitespace-pre-wrap text-muted-foreground">
                          {runningOutput || "Starting…"}
                        </pre>
                      )}
                      {runError?.taskId === t.id && (
                        <p className="mt-2 text-xs text-destructive">{runError.message}</p>
                      )}
                    </li>
                  )
                })
              )}
            </ul>
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
            <div className="max-h-80 overflow-y-auto">
              {audit.length === 0 ? (
                <p className="text-sm text-muted-foreground">No audit entries.</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {audit.map((e, i) => (
                    <li
                      key={i}
                      className={cn("flex items-baseline gap-2 border-b py-1 text-xs last:border-0 text-muted-foreground")}
                    >
                      <span className="shrink-0 font-mono">{(e.ts ?? "").slice(11, 19)}</span>
                      <span className="shrink-0 font-medium text-foreground">{e.agent ?? "?"}</span>
                      <span className="truncate">{auditLabel(e).slice(0, 60)}</span>
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

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <Card size="sm">
      <CardContent className="flex flex-col gap-1 px-4">
        <span className="text-xs tracking-wide text-muted-foreground uppercase">{label}</span>
        <span className="text-xl font-semibold">{value}</span>
      </CardContent>
    </Card>
  )
}
