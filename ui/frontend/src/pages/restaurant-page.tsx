import * as React from "react"
import {
  CheckIcon,
  ClipboardCopyIcon,
  DatabaseIcon,
  FolderUpIcon,
  Loader2Icon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

const ALLOWED = [".jpg", ".jpeg", ".png", ".webp", ".xlsx", ".xlsm", ".xls", ".csv", ".pdf"]
const MEATS = ["chicken", "beef"] as const
type Meat = (typeof MEATS)[number]

type DaySummary = { day: string; step: number; in_app: boolean; inbox_files: number }
type Job = { state: string; day?: string; message?: string }
type FolderRow = { day: string; state: string; message: string; photos: number; sheets: number }
type Overview = { days: DaySummary[]; default_day: string; last_day_in_app: string | null; backup: string | null; job: Job }
type Held = { line: number; name: string; alt: string | null; qty: number | null; qty_alt: number | null; guess: string | null; item: string | null; score: number | null }
type Expense = { label: string; amount: number; big: boolean }
type Closing = { trim_kg: number; main_kg: number; tallow_kg: number }
type MeatRead = { received_kg: number | null; spit_kg: number | null; sold_kg: number | null; waste_kg: number | null; closing: Closing | null }
type Questions = {
  figures: Record<string, string | number | null>
  cash: Record<string, number | null>
  meat: Partial<Record<Meat, MeatRead>>
  checks: string[]
  blockers: string[]
  held_lines: Held[]
  expenses: Expense[]
  notes: string[]
  warnings: string[]
}
type ExpenseRow = { label: string; amount: number; safe: boolean }
type Overrides = {
  cash?: Record<string, number>
  expenses?: ExpenseRow[]
  meat?: Partial<Record<Meat, Partial<Record<"received_kg" | "spit_kg" | "sold_kg" | "waste_kg", number>> & { closing?: Partial<Closing> }>>
  lines?: Record<string, { item?: string; qty?: number }>
}
type Answers = {
  approved_by?: string
  confirmed?: Record<string, boolean>
  overrides?: Overrides
  notes?: Record<string, string>
  paid_from_safe?: string[]
  cleared_blockers?: Record<string, string>
  allow_updates?: string[]
  held_lines_all_correct?: boolean
}
type UploadRecord = { at: string; committed: boolean; dry_run: boolean; verified: boolean | null; sql_sha: string; approved_by?: string }
type DayView = {
  day: string
  step: number
  in_app: boolean
  apply_sha: string | null
  upload: UploadRecord | null
  inbox: { name: string; size: number }[]
  source: string | null
  questions: Questions | null
  answers: Answers | null
  files: Record<string, boolean>
}
type FinishResult = { ok: boolean; status: string; reasons?: string[]; needs_human?: string[]; errors?: string[]; rehearsal?: { ok: boolean; checked: number }; learn_command?: string }
type UploadResult = { ok: boolean; status: string; committed?: boolean; dry_run?: boolean; verified?: boolean | null; checked?: number; not_found?: string[]; message?: string; details?: string[]; differences?: string[] }
type CheckResult = { ok: boolean; status: string; message?: string; field_counts?: Record<string, number>; differences?: string[]; source?: string }
type MenuItem = { id: string; name_ar: string; name_en?: string | null }
type DbStatus = { configured: boolean; driver: boolean; variable: string }

const STEPS = ["Files and draft", "Check each item", "Upload to the app", "Verify"]
const CASH_ITEMS: [string, string][] = [
  ["old_cash", "Opening cash"],
  ["pos_sales", "Sales"],
  ["wallet", "Wallet"],
  ["transfer_to_admin", "Transfer to admin"],
  ["actual_cash_count", "Cash counted (New)"],
]
const MEAT_ITEMS: [keyof MeatRead & ("received_kg" | "spit_kg" | "sold_kg" | "waste_kg"), string][] = [
  ["received_kg", "received (kg)"],
  ["spit_kg", "used on the spits (kg)"],
  ["sold_kg", "sold (kg)"],
  ["waste_kg", "waste (kg)"],
]

function fmt(v: string | number | null | undefined) {
  if (v === null || v === undefined || v === "") return "—"
  return typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(v)
}

async function api<T>(path: string, init?: RequestInit): Promise<{ status: number; data: T }> {
  const res = await fetch(path, init)
  let data: unknown = null
  try {
    data = await res.json()
  } catch {
    data = null
  }
  return { status: res.status, data: data as T }
}

async function filesFromDrop(dt: DataTransfer): Promise<File[]> {
  const out: File[] = []
  const walk = async (entry: FileSystemEntry): Promise<void> => {
    if (entry.isFile) {
      out.push(await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject)))
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
        if (!batch.length) break
        for (const e of batch) await walk(e)
      }
    }
  }
  const entries = Array.from(dt.items ?? [])
    .map((i) => (i.kind === "file" ? i.webkitGetAsEntry?.() : null))
    .filter((e): e is FileSystemEntry => !!e)
  if (entries.length) {
    for (const e of entries) await walk(e)
    return out
  }
  return Array.from(dt.files)
}

function StatusBadge({ confirmed, corrected }: { confirmed: boolean; corrected: boolean }) {
  if (corrected) return <Badge variant="default">Corrected</Badge>
  if (confirmed) return <Badge variant="secondary"><CheckIcon /> Confirmed</Badge>
  return <Badge variant="outline">To check</Badge>
}

function NumberCell({ value, onCommit, className }: { value: number | null | undefined; onCommit: (n: number | null) => void; className?: string }) {
  const [text, setText] = React.useState(value === null || value === undefined ? "" : String(value))
  React.useEffect(() => setText(value === null || value === undefined ? "" : String(value)), [value])
  return (
    <Input
      inputMode="decimal"
      className={cn("h-8 w-28 text-right", className)}
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => {
        const t = text.trim().replace(",", ".")
        if (t === "") return onCommit(null)
        const n = Number(t)
        if (Number.isFinite(n) && n >= 0) onCommit(n)
        else setText(value === null || value === undefined ? "" : String(value))
      }}
    />
  )
}

function NoteCell({ value, onSave, className }: { value: string | undefined; onSave: (t: string) => void; className?: string }) {
  const [text, setText] = React.useState(value ?? "")
  React.useEffect(() => setText(value ?? ""), [value])
  return <Input className={cn("h-8 w-56", className)} placeholder="Note (optional)" value={text} onChange={(e) => setText(e.target.value)} onBlur={() => text !== (value ?? "") && onSave(text)} />
}

function ItemRow(props: {
  label: string
  read: number | null | undefined
  value: number | null | undefined
  confirmed: boolean
  onConfirm: (on: boolean) => void
  onChange: (n: number | null) => void
  onReset: () => void
  note?: string
  onNote?: (t: string) => void
}) {
  const corrected = props.value !== props.read && props.value !== null && props.value !== undefined
  return (
    <div className="flex flex-wrap items-center gap-2 border-b py-1.5 last:border-b-0">
      <label className="flex w-52 items-center gap-2">
        <input type="checkbox" checked={props.confirmed || corrected} disabled={corrected} onChange={(e) => props.onConfirm(e.target.checked)} />
        <span>{props.label}</span>
      </label>
      <NumberCell value={props.value} onCommit={props.onChange} />
      <span className="w-28 text-xs text-muted-foreground">David read {fmt(props.read)}</span>
      <StatusBadge confirmed={props.confirmed} corrected={corrected} />
      {corrected && (
        <Button variant="ghost" size="icon-xs" aria-label="Back to what David read" onClick={props.onReset}>
          <RotateCcwIcon />
        </Button>
      )}
      {props.onNote && <NoteCell value={props.note} onSave={props.onNote} />}
    </div>
  )
}

export function RestaurantPage() {
  const [overview, setOverview] = React.useState<Overview | null>(null)
  const [folders, setFolders] = React.useState<FolderRow[]>([])
  const [day, setDay] = React.useState("")
  const [view, setView] = React.useState<DayView | null>(null)
  const [answers, setAnswers] = React.useState<Answers>({})
  const [job, setJob] = React.useState<Job>({ state: "idle" })
  const [error, setError] = React.useState<string | null>(null)
  const [uploading, setUploading] = React.useState<{ done: number; total: number; skipped: number } | null>(null)
  const [dragging, setDragging] = React.useState(false)
  const [finish, setFinish] = React.useState<FinishResult | null>(null)
  const [finishing, setFinishing] = React.useState(false)
  const [shown, setShown] = React.useState<string>("apply")
  const [fileText, setFileText] = React.useState<string>("")
  const [planText, setPlanText] = React.useState<string>("")
  const [copied, setCopied] = React.useState(false)
  const [check, setCheck] = React.useState<CheckResult | null>(null)
  const [checking, setChecking] = React.useState(false)
  const [menu, setMenu] = React.useState<MenuItem[]>([])
  const [db, setDb] = React.useState<DbStatus | null>(null)
  const [dbTest, setDbTest] = React.useState<string | null>(null)
  const [pushing, setPushing] = React.useState<null | "dry" | "real">(null)
  const [pushResult, setPushResult] = React.useState<UploadResult | null>(null)
  const [confirmOpen, setConfirmOpen] = React.useState(false)
  const [typedDay, setTypedDay] = React.useState("")
  const folderInput = React.useRef<HTMLInputElement | null>(null)
  const fileInput = React.useRef<HTMLInputElement | null>(null)

  const loadOverview = React.useCallback(async () => {
    const r = await api<Overview>("/api/restaurant/overview")
    if (r.status === 200) {
      setOverview(r.data)
      setJob(r.data.job)
      return r.data
    }
    return null
  }, [])

  const loadDay = React.useCallback(async (d: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return
    const r = await api<DayView>(`/api/restaurant/day/${d}`)
    if (r.status === 200) {
      setView(r.data)
      setAnswers(r.data.answers ?? {})
      setError(null)
    } else {
      setError("Couldn't load that day.")
    }
  }, [])

  React.useEffect(() => {
    void loadOverview().then((o) => {
      if (o) {
        setDay(o.default_day)
        void loadDay(o.default_day)
      }
    })
    void api<{ items: MenuItem[] }>("/api/restaurant/menu").then((r) => r.status === 200 && setMenu(r.data.items))
    void api<DbStatus>("/api/restaurant/db/status").then((r) => r.status === 200 && setDb(r.data))
  }, [loadOverview, loadDay])

  React.useEffect(() => {
    const load = () => void api<{ folders: FolderRow[] }>("/api/restaurant/folders").then((r) => r.status === 200 && setFolders(r.data.folders))
    load()
    const t = window.setInterval(load, 60000)
    return () => window.clearInterval(t)
  }, [])

  React.useEffect(() => {
    if (job.state !== "running") return
    const t = window.setInterval(async () => {
      const r = await api<Job>("/api/restaurant/job")
      if (r.status === 200) {
        setJob(r.data)
        if (r.data.state !== "running") {
          void loadOverview()
          if (r.data.day) void loadDay(r.data.day)
        }
      }
    }, 2000)
    return () => window.clearInterval(t)
  }, [job.state, loadDay, loadOverview])

  React.useEffect(() => {
    if (!view?.files[shown]) {
      setFileText("")
      return
    }
    void api<{ text: string }>(`/api/restaurant/day/${view.day}/file/${shown}`).then((r) => setFileText(r.status === 200 ? r.data.text : ""))
  }, [shown, view?.day, view?.files, finish])

  React.useEffect(() => {
    if (!view?.files.plan) {
      setPlanText("")
      return
    }
    void api<{ text: string }>(`/api/restaurant/day/${view.day}/file/plan`).then((r) => setPlanText(r.status === 200 ? r.data.text : ""))
  }, [view?.day, view?.files.plan, finish, pushResult])

  const pickDay = (d: string) => {
    setDay(d)
    setFinish(null)
    setCheck(null)
    setPushResult(null)
    void loadDay(d)
  }

  const save = async (next: Answers) => {
    setAnswers(next)
    if (!view) return
    await api(`/api/restaurant/day/${view.day}/answers`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(next) })
  }

  const upload = async (list: File[]) => {
    if (!view) return
    const ok = list.filter((f) => ALLOWED.includes(f.name.slice(f.name.lastIndexOf(".")).toLowerCase()))
    setUploading({ done: 0, total: ok.length, skipped: list.length - ok.length })
    let failed = 0
    for (let i = 0; i < ok.length; i++) {
      const r = await api(`/api/restaurant/upload?day=${view.day}&name=${encodeURIComponent(ok[i].name)}`, { method: "PUT", body: ok[i] })
      if (r.status !== 200) failed++
      setUploading({ done: i + 1, total: ok.length, skipped: list.length - ok.length })
    }
    setUploading(null)
    if (failed) setError(`${failed} file(s) were not accepted.`)
    await loadDay(view.day)
    await loadOverview()
  }

  const startPrepare = async () => {
    if (!view) return
    setError(null)
    const r = await api<Job & { detail?: string }>("/api/restaurant/prepare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ day: view.day }) })
    if (r.status === 200) setJob(r.data)
    else setError(r.data?.detail ?? "Couldn't start reading the day.")
  }

  const clearInbox = async () => {
    if (!view || !window.confirm(`Remove the files you uploaded for ${view.day}? Your drafts and SQL files stay.`)) return
    await api(`/api/restaurant/inbox/${view.day}`, { method: "DELETE" })
    await loadDay(view.day)
    await loadOverview()
  }

  const runFinish = async () => {
    if (!view) return
    setFinishing(true)
    setFinish(null)
    setPushResult(null)
    const r = await api<FinishResult>(`/api/restaurant/day/${view.day}/finish`, { method: "POST" })
    setFinish(r.data)
    setFinishing(false)
    setShown("apply")
    await loadDay(view.day)
    await loadOverview()
  }

  const runCheck = async () => {
    if (!view) return
    setChecking(true)
    const live = db?.configured ? "?live=true" : ""
    const r = await api<CheckResult>(`/api/restaurant/day/${view.day}/check${live}`, { method: "POST" })
    setCheck(r.data)
    setChecking(false)
    await loadDay(view.day)
    await loadOverview()
  }

  const testDb = async () => {
    setDbTest("Testing…")
    const r = await api<{ ok: boolean; days?: number; last_day?: string | null; message?: string }>("/api/restaurant/db/test", { method: "POST" })
    setDbTest(r.data.ok ? `Connected. The app holds ${r.data.days} days, the newest is ${r.data.last_day ?? "none"}.` : (r.data.message ?? "Couldn't connect."))
  }

  const push = async (dry: boolean) => {
    if (!view?.apply_sha) return
    setPushing(dry ? "dry" : "real")
    setPushResult(null)
    const r = await api<UploadResult>(`/api/restaurant/day/${view.day}/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: dry, confirm_day: view.day, sql_sha: view.apply_sha }),
    })
    setPushResult(r.data)
    setPushing(null)
    setConfirmOpen(false)
    setTypedDay("")
    await loadDay(view.day)
    await loadOverview()
  }

  const copy = async () => {
    await navigator.clipboard.writeText(fileText)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1500)
  }

  const q = view?.questions ?? null
  const ov: Overrides = answers.overrides ?? {}
  const confirmed = answers.confirmed ?? {}
  const running = job.state === "running"
  const step = view?.upload?.committed ? 4 : view?.step ?? 1

  const setOv = (next: Overrides) => {
    const clean: Overrides = {}
    if (next.cash && Object.keys(next.cash).length) clean.cash = next.cash
    if (next.expenses) clean.expenses = next.expenses
    if (next.meat) {
      const m: NonNullable<Overrides["meat"]> = {}
      for (const k of MEATS) {
        const v = next.meat[k]
        if (v && Object.keys(v).length) m[k] = v
      }
      if (Object.keys(m).length) clean.meat = m
    }
    if (next.lines && Object.keys(next.lines).length) clean.lines = next.lines
    void save({ ...answers, overrides: clean })
  }
  const setNote = (key: string, text: string) => {
    const n = { ...(answers.notes ?? {}) }
    if (text.trim()) n[key] = text.trim()
    else delete n[key]
    void save({ ...answers, notes: n })
  }
  const noteProps = (key: string) => ({ note: answers.notes?.[key], onNote: (t: string) => setNote(key, t) })
  const setConfirm = (key: string, on: boolean) => {
    const c = { ...confirmed }
    if (on) c[key] = true
    else delete c[key]
    void save({ ...answers, confirmed: c })
  }

  const cashRead = (k: string) => q?.cash?.[k] ?? null
  const cashValue = (k: string) => ov.cash?.[k] ?? cashRead(k)
  const setCash = (k: string, n: number | null) => {
    const cash = { ...(ov.cash ?? {}) }
    if (n === null || n === cashRead(k)) delete cash[k]
    else cash[k] = n
    setOv({ ...ov, cash })
  }

  const expRows: ExpenseRow[] = ov.expenses ?? (q?.expenses ?? []).map((e) => ({ label: e.label, amount: e.amount, safe: (answers.paid_from_safe ?? []).includes(e.label) }))
  const setExp = (rows: ExpenseRow[]) => {
    const same = q && rows.length === q.expenses.length && rows.every((r, i) => r.label === q.expenses[i].label && r.amount === q.expenses[i].amount)
    const safeLabels = rows.filter((r) => r.safe).map((r) => r.label)
    const next: Overrides = { ...ov }
    if (same) delete next.expenses
    else next.expenses = rows
    void save({ ...answers, overrides: next, paid_from_safe: safeLabels })
  }

  const meatRead = (m: Meat, k: keyof MeatRead) => (q?.meat?.[m]?.[k] as number | null | undefined) ?? null
  const meatValue = (m: Meat, k: "received_kg" | "spit_kg" | "sold_kg" | "waste_kg") => ov.meat?.[m]?.[k] ?? meatRead(m, k)
  const setMeat = (m: Meat, k: "received_kg" | "spit_kg" | "sold_kg" | "waste_kg", n: number | null) => {
    const cur = { ...(ov.meat?.[m] ?? {}) }
    if (n === null || n === meatRead(m, k)) delete cur[k]
    else cur[k] = n
    setOv({ ...ov, meat: { ...(ov.meat ?? {}), [m]: cur } })
  }
  const closingValue = (m: Meat, k: keyof Closing) => ov.meat?.[m]?.closing?.[k] ?? q?.meat?.[m]?.closing?.[k] ?? null
  const setClosing = (m: Meat, k: keyof Closing, n: number | null) => {
    const cur = { ...(ov.meat?.[m] ?? {}) }
    const cl = { ...(cur.closing ?? {}) }
    if (n === null || n === q?.meat?.[m]?.closing?.[k]) delete cl[k]
    else cl[k] = n
    if (Object.keys(cl).length) cur.closing = cl
    else delete cur.closing
    setOv({ ...ov, meat: { ...(ov.meat ?? {}), [m]: cur } })
  }

  const allKeys: string[] = React.useMemo(() => {
    if (!q) return []
    const keys = CASH_ITEMS.map(([k]) => `cash.${k}`)
    q.expenses.forEach((_, i) => keys.push(`expense.${i}`))
    for (const m of MEATS) {
      if (q.meat?.[m]) {
        MEAT_ITEMS.forEach(([k]) => keys.push(`meat.${m}.${k}`))
        if (q.meat[m]?.closing) keys.push(`meat.${m}.closing`)
      }
    }
    q.checks.forEach((_, i) => keys.push(`check.${i}`))
    q.held_lines.forEach((h) => keys.push(`line.${h.line}`))
    return keys
  }, [q])
  const isCorrected = (key: string): boolean => {
    const [a, b, c] = key.split(".")
    if (a === "cash") return ov.cash?.[key.slice(5)] !== undefined
    if (a === "expense") {
      const i = Number(b)
      const r = ov.expenses?.[i]
      const read = q?.expenses[i]
      return !!r && !!read && (r.label !== read.label || r.amount !== read.amount)
    }
    if (a === "meat") return c === "closing" ? !!ov.meat?.[b as Meat]?.closing : ov.meat?.[b as Meat]?.[c as "received_kg"] !== undefined
    if (a === "line") return ov.lines?.[b] !== undefined
    return false
  }
  const settled = allKeys.filter((k) => confirmed[k] === true || isCorrected(k)).length
  const markAll = () => {
    const c = { ...confirmed }
    allKeys.forEach((k) => (c[k] = true))
    void save({ ...answers, confirmed: c })
  }

  const setLine = (h: Held, patch: { item?: string; qty?: number | null }) => {
    const lines = { ...(ov.lines ?? {}) }
    const cur = { ...(lines[String(h.line)] ?? {}) }
    if ("item" in patch) {
      if (!patch.item || patch.item === h.item) delete cur.item
      else cur.item = patch.item
    }
    if ("qty" in patch) {
      if (patch.qty === null || patch.qty === undefined || patch.qty === h.qty) delete cur.qty
      else cur.qty = patch.qty
    }
    if (Object.keys(cur).length) lines[String(h.line)] = cur
    else delete lines[String(h.line)]
    setOv({ ...ov, lines })
  }

  const planOps = planText
    .split("\n")
    .filter((l) => /^- (insert|update) /.test(l))
    .map((l) => l.slice(2))
  const uploaded = !!view?.upload?.committed
  const canPush = !!view?.apply_sha && !!db?.configured && !running

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-xl font-semibold">Restaurant: daily entry</h1>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Day</span>
          <Input type="date" value={day} onChange={(e) => setDay(e.target.value)} className="w-40" />
          <Button variant="outline" onClick={() => pickDay(day)}>
            Load
          </Button>
          <Button variant="ghost" size="icon" aria-label="Refresh" onClick={() => void loadOverview().then(() => view && loadDay(view.day))}>
            <RefreshCwIcon />
          </Button>
        </div>
      </div>

      {overview && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>
            In the app up to {overview.last_day_in_app ?? "nothing yet"} (backup {overview.backup ?? "none"}).
          </span>
          {overview.days.slice(0, 8).map((d) => (
            <button key={d.day} onClick={() => pickDay(d.day)} className="rounded-full">
              <Badge variant={d.day === view?.day ? "default" : "outline"}>
                {d.day.slice(5)} {d.in_app ? "in app" : `step ${d.step}`}
              </Badge>
            </button>
          ))}
        </div>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertTitle>Something went wrong</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {STEPS.map((s, i) => {
          const n = i + 1
          const state = n < step ? "done" : n === step ? "now" : "waiting"
          return (
            <Card key={s} size="sm" className={cn(state === "now" && "ring-2 ring-primary")}>
              <CardContent className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">
                  {n} {s}
                </span>
                <span className={cn("text-sm font-medium", state === "waiting" && "text-muted-foreground")}>
                  {state === "done" ? "Done" : state === "now" ? (n === 2 && allKeys.length ? `${settled} of ${allKeys.length} items settled` : "In progress") : "Waiting"}
                </span>
              </CardContent>
            </Card>
          )
        })}
      </div>

      <Card size="sm">
        <CardHeader>
          <CardTitle>Day folders in Demo Bistro</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-1 text-sm">
          {folders.length === 0 && <span className="text-muted-foreground">No recent day folder yet.</span>}
          {folders.map((f) => (
            <div key={f.day} className="flex flex-wrap items-center gap-2 border-b py-1 last:border-b-0">
              <button className="font-medium underline-offset-2 hover:underline" onClick={() => pickDay(f.day)}>{f.day}</button>
              <Badge variant={f.state === "ready" ? "default" : f.state === "waiting" || f.state === "arriving" ? "outline" : "secondary"}>{f.state}</Badge>
              <span className="text-muted-foreground">{f.photos} photos, {f.sheets} sheets</span>
              <span>{f.message}</span>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>1. The day's files</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={async (e) => {
              e.preventDefault()
              setDragging(false)
              await upload(await filesFromDrop(e.dataTransfer))
            }}
            className={cn("flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center", dragging && "border-primary bg-muted")}
          >
            <FolderUpIcon className="size-6 text-muted-foreground" />
            <p className="text-sm">Drop the day's whole folder here: photos, the Excel sheets, everything.</p>
            <p className="text-xs text-muted-foreground">Files are copied into David's own inbox for {view?.day ?? "the day"}. Your Demo Bistro folder is never touched.</p>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => folderInput.current?.click()}>
                <FolderUpIcon data-icon="inline-start" /> Choose a folder
              </Button>
              <Button variant="outline" onClick={() => fileInput.current?.click()}>
                <UploadIcon data-icon="inline-start" /> Choose files
              </Button>
            </div>
            <input
              ref={folderInput}
              type="file"
              className="hidden"
              multiple
              {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
              onChange={(e) => {
                void upload(Array.from(e.target.files ?? []))
                e.target.value = ""
              }}
            />
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              multiple
              onChange={(e) => {
                void upload(Array.from(e.target.files ?? []))
                e.target.value = ""
              }}
            />
          </div>
          {uploading && (
            <p className="text-sm">
              <Loader2Icon className="mr-1 inline size-4 animate-spin" /> Uploading {uploading.done} of {uploading.total}
              {uploading.skipped ? ` (${uploading.skipped} skipped: not a photo, sheet or PDF)` : ""}
            </p>
          )}
          {view && view.inbox.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">
                {view.inbox.length} file(s) uploaded for {view.day}:
              </span>
              {view.inbox.slice(0, 14).map((f) => (
                <Badge key={f.name} variant="secondary" title={`${Math.round(f.size / 1024)} KB`}>
                  {f.name.length > 28 ? f.name.slice(0, 26) + "…" : f.name}
                </Badge>
              ))}
              {view.inbox.length > 14 && <span className="text-muted-foreground">and {view.inbox.length - 14} more</span>}
              <Button variant="ghost" size="sm" onClick={() => void clearInbox()}>
                <Trash2Icon data-icon="inline-start" /> Remove uploads
              </Button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void startPrepare()} disabled={running || !view}>
              {running ? <Loader2Icon data-icon="inline-start" className="animate-spin" /> : <PlayIcon data-icon="inline-start" />}
              {q ? "Read the day again" : "Read the day"}
            </Button>
            <span className="text-sm text-muted-foreground">
              {running
                ? `${job.day}: ${job.message ?? "working"}`
                : job.state === "error"
                  ? `Stopped: ${job.message}`
                  : view?.source
                    ? `The draft was made from the ${view.source === "inbox" ? "uploaded files" : "Demo Bistro folder"}.`
                    : "If the day's folder is already in the Demo Bistro folder you can read it without uploading."}
            </span>
          </div>
        </CardContent>
      </Card>

      {q && (
        <>
          {q.warnings.length > 0 && (
            <Alert>
              <AlertTitle>Look at these first</AlertTitle>
              <AlertDescription>
                <ul className="list-disc pl-4">
                  {q.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </AlertDescription>
            </Alert>
          )}

          <Card>
            <CardHeader>
              <CardTitle>2. Check each item: tick it if it's right, or type the right number</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4 text-sm">
              <div className="flex flex-wrap items-center gap-3">
                <span className="font-medium">
                  {settled} of {allKeys.length} items settled
                </span>
                <Button variant="outline" size="sm" onClick={markAll}>
                  <CheckIcon data-icon="inline-start" /> Tick everything I haven't changed
                </Button>
                <span className="text-xs text-muted-foreground">Typing a number in any box corrects it. The arrow puts David's reading back. Every row has a note box for anything you want written down (it goes into the plan).</span>
              </div>

              <section>
                <h3 className="mb-1 font-medium">Cash</h3>
                {CASH_ITEMS.map(([k, label]) => (
                  <ItemRow
                    key={k}
                    label={label}
                    read={cashRead(k)}
                    value={cashValue(k)}
                    confirmed={confirmed[`cash.${k}`] === true}
                    onConfirm={(on) => setConfirm(`cash.${k}`, on)}
                    onChange={(n) => setCash(k, n)}
                    onReset={() => setCash(k, null)}
                    {...noteProps(`cash.${k}`)}
                  />
                ))}
                {(q.figures.chicken_workbook || q.figures.beef_workbook) && (
                  <div className="mt-2 flex flex-col gap-1 text-xs text-muted-foreground">
                    {q.figures.chicken_workbook && <span>Chicken: {q.figures.chicken_workbook}</span>}
                    {q.figures.beef_workbook && <span>Beef: {q.figures.beef_workbook}</span>}
                  </div>
                )}
                <div className="mt-1 text-xs text-muted-foreground">
                  Expected {fmt(q.figures.expected)} · counted {fmt(q.figures.counted)} · difference {fmt(q.figures.diff)} (recomputed from your numbers when you make the SQL)
                </div>
              </section>

              <section>
                <h3 className="mb-1 font-medium">Expenses</h3>
                {expRows.map((r, i) => {
                  const read = q.expenses[i]
                  const changed = !read || read.label !== r.label || read.amount !== r.amount
                  return (
                    <div key={i} className="flex flex-wrap items-center gap-2 border-b py-1.5 last:border-b-0">
                      <input type="checkbox" checked={(confirmed[`expense.${i}`] === true && r.amount != null) || changed} disabled={changed || r.amount == null} title={r.amount == null ? "Type the amount first" : undefined} onChange={(e) => setConfirm(`expense.${i}`, e.target.checked)} aria-label={`Expense ${i + 1} is right`} />
                      <Input className="h-8 w-44" value={r.label} onChange={(e) => setExp(expRows.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                      <NumberCell value={r.amount} onCommit={(n) => n !== null && setExp(expRows.map((x, j) => (j === i ? { ...x, amount: n } : x)))} />
                      {r.amount == null && <span className="text-xs text-destructive">type the amount first</span>}
                      <button onClick={() => setExp(expRows.map((x, j) => (j === i ? { ...x, safe: !x.safe } : x)))}>
                        <Badge variant={r.safe ? "default" : "outline"}>{r.safe ? "paid from the safe" : "paid from the register"}</Badge>
                      </button>
                      <StatusBadge confirmed={confirmed[`expense.${i}`] === true && r.amount != null} corrected={changed} />
                      {read && <span className="text-xs text-muted-foreground">David read {read.label} {fmt(read.amount)}</span>}
                      <NoteCell value={answers.notes?.[`expense.${i}`]} onSave={(t) => setNote(`expense.${i}`, t)} />
                      <Button variant="ghost" size="icon-xs" aria-label="Remove this expense" onClick={() => setExp(expRows.filter((_, j) => j !== i))}>
                        <Trash2Icon />
                      </Button>
                    </div>
                  )
                })}
                <div className="mt-2">
                  <Button variant="outline" size="sm" onClick={() => setExp([...expRows, { label: "", amount: 0, safe: false }])}>
                    <PlusIcon data-icon="inline-start" /> Add an expense
                  </Button>
                </div>
              </section>

              {MEATS.map((m) =>
                q.meat?.[m] ? (
                  <section key={m}>
                    <h3 className="mb-1 font-medium capitalize">{m}</h3>
                    {MEAT_ITEMS.map(([k, label]) => (
                      <ItemRow
                        key={k}
                        label={label}
                        read={meatRead(m, k)}
                        value={meatValue(m, k)}
                        confirmed={confirmed[`meat.${m}.${k}`] === true}
                        onConfirm={(on) => setConfirm(`meat.${m}.${k}`, on)}
                        onChange={(n) => setMeat(m, k, n)}
                        onReset={() => setMeat(m, k, null)}
                        {...noteProps(`meat.${m}.${k}`)}
                      />
                    ))}
                    {q.meat[m]?.closing && (
                      <div className="flex flex-wrap items-center gap-2 border-b py-1.5 last:border-b-0">
                        <label className="flex w-52 items-center gap-2">
                          <input type="checkbox" checked={confirmed[`meat.${m}.closing`] === true || isCorrected(`meat.${m}.closing`)} disabled={isCorrected(`meat.${m}.closing`)} onChange={(e) => setConfirm(`meat.${m}.closing`, e.target.checked)} />
                          <span>closing weights (kg)</span>
                        </label>
                        {(["trim_kg", "main_kg", "tallow_kg"] as const).map((k) => (
                          <span key={k} className="flex items-center gap-1 text-xs text-muted-foreground">
                            {k.replace("_kg", "")}
                            <NumberCell className="w-20" value={closingValue(m, k)} onCommit={(n) => setClosing(m, k, n)} />
                          </span>
                        ))}
                        <StatusBadge confirmed={confirmed[`meat.${m}.closing`] === true} corrected={isCorrected(`meat.${m}.closing`)} />
                        <NoteCell value={answers.notes?.[`meat.${m}.closing`]} onSave={(t) => setNote(`meat.${m}.closing`, t)} />
                      </div>
                    )}
                  </section>
                ) : null
              )}

              {q.checks.length > 0 && (
                <section>
                  <h3 className="mb-1 font-medium">Warnings in the draft</h3>
                  {q.checks.map((c, i) => (
                    <div key={c} className="flex flex-wrap items-start gap-2 border-b py-1.5 last:border-b-0">
                      <label className="flex min-w-64 flex-1 items-start gap-2">
                        <input type="checkbox" className="mt-1" checked={confirmed[`check.${i}`] === true} onChange={(e) => setConfirm(`check.${i}`, e.target.checked)} />
                        <span>{c.replace(/^CHECK: /, "")}</span>
                      </label>
                      <NoteCell className="w-72" value={answers.notes?.[`check.${i}`]} onSave={(t) => setNote(`check.${i}`, t)} />
                    </div>
                  ))}
                </section>
              )}

              {q.blockers.length > 0 && (
                <section className="flex flex-col gap-2">
                  <h3 className="font-medium">Blockers: each needs a reason in your own words</h3>
                  {q.blockers.map((b, i) => (
                    <div key={b} className="flex flex-col gap-1">
                      <span className="text-muted-foreground">{b.replace(/^BLOCKER: /, "")}</span>
                      <Input
                        placeholder="Why this is fine"
                        value={answers.cleared_blockers?.[String(i + 1)] ?? ""}
                        onChange={(e) => setAnswers({ ...answers, cleared_blockers: { ...(answers.cleared_blockers ?? {}), [String(i + 1)]: e.target.value } })}
                        onBlur={() => void save(answers)}
                      />
                    </div>
                  ))}
                </section>
              )}

              {q.held_lines.length > 0 && (
                <section>
                  <h3 className="mb-1 font-medium">Item lines David isn't sure of</h3>
                  {q.held_lines.map((h) => {
                    const fix = ov.lines?.[String(h.line)]
                    return (
                      <div key={h.line} className="flex flex-wrap items-center gap-2 border-b py-1.5 last:border-b-0">
                        <input type="checkbox" checked={confirmed[`line.${h.line}`] === true || !!fix} disabled={!!fix} onChange={(e) => setConfirm(`line.${h.line}`, e.target.checked)} aria-label={`Line ${h.line} is right`} />
                        <span className="w-56" dir="auto">
                          {h.name}
                          {h.alt ? ` / ${h.alt}` : ""}
                        </span>
                        <select
                          className="h-8 max-w-64 rounded-lg border bg-background px-2 text-sm"
                          value={fix?.item ?? h.item ?? ""}
                          onChange={(e) => setLine(h, { item: e.target.value })}
                          aria-label="Which menu item is it"
                        >
                          {!h.item && <option value="">choose an item</option>}
                          {menu.map((m) => (
                            <option key={m.id} value={m.id}>
                              {m.name_ar}
                              {m.name_en ? ` (${m.name_en})` : ""}
                            </option>
                          ))}
                        </select>
                        <NumberCell className="w-20" value={fix?.qty ?? h.qty} onCommit={(n) => setLine(h, { qty: n })} />
                        <StatusBadge confirmed={confirmed[`line.${h.line}`] === true} corrected={!!fix} />
                        <NoteCell value={answers.notes?.[`line.${h.line}`]} onSave={(t) => setNote(`line.${h.line}`, t)} />
                        {fix && (
                          <Button variant="ghost" size="icon-xs" aria-label="Back to David's guess" onClick={() => setLine(h, { item: h.item ?? "", qty: null })}>
                            <RotateCcwIcon />
                          </Button>
                        )}
                      </div>
                    )
                  })}
                  <p className="mt-1 text-xs text-muted-foreground">Lines don't change the SQL. What you confirm or correct here is what David learns (run the command shown after you make the SQL).</p>
                </section>
              )}

              <div className="flex items-center gap-2">
                <span>Approved by</span>
                <Input className="w-48" value={answers.approved_by ?? ""} onChange={(e) => setAnswers({ ...answers, approved_by: e.target.value })} onBlur={() => void save(answers)} />
              </div>
              {q.notes.length > 0 && (
                <ul className="list-disc pl-6 text-xs text-muted-foreground">
                  {q.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>3. Make the SQL, then upload to the app</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => void runFinish()} disabled={finishing}>
                  {finishing && <Loader2Icon data-icon="inline-start" className="animate-spin" />}
                  Make the SQL files
                </Button>
                <span className="text-muted-foreground">David rehearses the file on a throw-away copy first. Your corrections are listed in the plan.</span>
              </div>
              {finish && !finish.ok && (
                <Alert variant="destructive">
                  <AlertTitle>No SQL was made{finish.status === "answers_missing" ? ": items are still open" : `: ${finish.status.replace(/_/g, " ")}`}</AlertTitle>
                  <AlertDescription>
                    <ul className="list-disc pl-4">
                      {[...(finish.errors ?? []), ...(finish.reasons ?? []), ...(finish.needs_human ?? [])].map((e) => (
                        <li key={e}>{e}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              {finish?.ok && finish.status === "nothing_to_do" && (
                <Alert>
                  <AlertTitle>Nothing to do</AlertTitle>
                  <AlertDescription>The app already holds exactly what this draft says.</AlertDescription>
                </Alert>
              )}
              {finish?.ok && finish.rehearsal && <p className="text-muted-foreground">Rehearsal passed: {finish.rehearsal.checked} check lines found, running it twice changes nothing, undo restores everything.</p>}
              {finish?.learn_command && <code className="rounded bg-muted p-2 text-xs">{finish.learn_command}</code>}

              {view?.files.apply && (
                <>
                  <div className="flex flex-col gap-2 rounded-lg border p-3">
                    <div className="flex flex-wrap items-center gap-2 font-medium">
                      <DatabaseIcon className="size-4" /> Upload to the app
                      {uploaded && <Badge variant="secondary"><CheckIcon /> Uploaded {view.upload?.at.replace("T", " ")}{view.upload?.verified ? ", verified" : ""}</Badge>}
                    </div>
                    {db && !db.configured && (
                      <Alert>
                        <AlertTitle>The upload is switched off</AlertTitle>
                        <AlertDescription>
                          <p>It needs the app database's connection string in a Windows setting called {db.variable}. You set it yourself, once; David never shows it or saves it anywhere else.</p>
                          <p>1. Open https://supabase.com/dashboard/project/olfuhcovdjmrfkibkxck/settings/database and copy the connection string (URI), the "Session pooler" one.</p>
                          <p>2. In PowerShell, replace the part in quotes with it and run (only here, never in a chat):</p>
                          <code className="block rounded bg-muted p-2 text-xs break-all">[Environment]::SetEnvironmentVariable("{db.variable}", "PASTE_THE_CONNECTION_STRING_HERE", "User")</code>
                          <p>3. Close David and open it again. Until then, copy apply.sql below and run it in the SQL editor as before.</p>
                          {!db.driver && <p>The Postgres driver is not installed on this computer yet.</p>}
                        </AlertDescription>
                      </Alert>
                    )}
                    <div className="flex flex-wrap items-center gap-2">
                      <Button variant="outline" size="sm" onClick={() => void testDb()} disabled={!db?.configured}>
                        Test the connection
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void push(true)} disabled={!canPush || pushing !== null}>
                        {pushing === "dry" && <Loader2Icon data-icon="inline-start" className="animate-spin" />}
                        Dry run (runs it on the app, then undoes it)
                      </Button>
                      <Button onClick={() => setConfirmOpen(true)} disabled={!canPush || pushing !== null || uploaded}>
                        <UploadIcon data-icon="inline-start" /> Upload to the app
                      </Button>
                      {dbTest && <span className="text-muted-foreground">{dbTest}</span>}
                    </div>
                    {pushResult && (
                      <Alert variant={pushResult.ok ? "default" : "destructive"}>
                        <AlertTitle>
                          {pushResult.status === "committed"
                            ? pushResult.verified
                              ? "Uploaded and verified: the app now holds exactly this day"
                              : "Uploaded, but the check afterwards found differences"
                            : pushResult.status === "dry_run_ok"
                              ? "Dry run passed: every check line was found, nothing was saved"
                              : pushResult.status === "already_in_app"
                                ? "Nothing to upload"
                                : "Not uploaded"}
                        </AlertTitle>
                        <AlertDescription>
                          {pushResult.message && <p>{pushResult.message}</p>}
                          {(pushResult.not_found ?? []).length > 0 && <p>Not found: {pushResult.not_found?.join(", ")}</p>}
                          <ul className="list-disc pl-4">
                            {[...(pushResult.details ?? []), ...(pushResult.differences ?? [])].map((d) => (
                              <li key={d}>{d}</li>
                            ))}
                          </ul>
                        </AlertDescription>
                      </Alert>
                    )}
                  </div>

                  <div className="flex flex-col gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {[
                        ["apply", "apply.sql"],
                        ["plan", "What it does"],
                        ["steps", "Run it by hand instead"],
                        ["undo", "undo.sql"],
                      ].map(([k, label]) => (
                        <Button key={k} size="sm" variant={shown === k ? "default" : "outline"} onClick={() => setShown(k)}>
                          {label}
                        </Button>
                      ))}
                      <Button size="sm" variant="secondary" onClick={() => void copy()}>
                        {copied ? <CheckIcon data-icon="inline-start" /> : <ClipboardCopyIcon data-icon="inline-start" />}
                        {copied ? "Copied" : "Copy to paste"}
                      </Button>
                    </div>
                    <pre className="max-h-96 overflow-auto rounded-lg border bg-muted/40 p-3 text-xs whitespace-pre-wrap">{fileText}</pre>
                  </div>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>4. Check the app against the draft</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-3 text-sm">
              <p className="text-muted-foreground">{db?.configured ? "David reads the app's database directly; no backup export is needed." : "Export a fresh backup first (the command is in 'Run it by hand instead'), then press Check."}</p>
              <div>
                <Button variant="outline" onClick={() => void runCheck()} disabled={checking || !view?.files.apply}>
                  {checking && <Loader2Icon data-icon="inline-start" className="animate-spin" />}
                  Check the app against the draft
                </Button>
              </div>
              {check && (
                <Alert variant={check.ok ? "default" : "destructive"}>
                  <AlertTitle>{check.ok ? "The app holds exactly what the draft said" : check.status === "not_in_backup" ? "Not in the backup yet" : "Differences found"}</AlertTitle>
                  <AlertDescription>
                    {check.message && <p>{check.message}</p>}
                    {check.field_counts && <p>Fields compared: {Object.entries(check.field_counts).map(([k, v]) => `${v} ${k}`).join(", ")}</p>}
                    <ul className="list-disc pl-4">
                      {(check.differences ?? []).map((d) => (
                        <li key={d}>{d}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>
        </>
      )}

      <Dialog open={confirmOpen} onOpenChange={(o) => { setConfirmOpen(o); if (!o) setTypedDay("") }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Upload {view?.day} to the app?</DialogTitle>
            <DialogDescription>This writes to the app's real database, in one step: all of it or none of it. David has already rehearsed it and will check the live tables afterwards.</DialogDescription>
          </DialogHeader>
          <div className="flex max-h-60 flex-col gap-1 overflow-auto text-sm">
            {planOps.map((o) => (
              <div key={o} className="flex items-start gap-2">
                <PencilIcon className="mt-0.5 size-3 shrink-0 text-muted-foreground" />
                <span>{o}</span>
              </div>
            ))}
            {planOps.length === 0 && <span className="text-muted-foreground">See "What it does".</span>}
          </div>
          <div className="flex flex-col gap-1 text-sm">
            <span>
              Approved by <b>{answers.approved_by || "nobody yet"}</b>. Type the day to confirm: <code>{view?.day}</code>
            </span>
            <Input value={typedDay} onChange={(e) => setTypedDay(e.target.value)} placeholder={view?.day} />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => void push(false)} disabled={typedDay !== view?.day || !answers.approved_by?.trim() || pushing !== null}>
              {pushing === "real" && <Loader2Icon data-icon="inline-start" className="animate-spin" />}
              Upload now
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
