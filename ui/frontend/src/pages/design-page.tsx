import * as React from "react"
import { CheckIcon, FolderPlusIcon, Loader2Icon, PencilIcon, PlayIcon, RefreshCwIcon, Trash2Icon, UploadIcon, Undo2Icon, XIcon } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

type ParamSpec = { type: string; required?: boolean; values?: string[]; min?: number; max?: number; default?: unknown }
type Operation = { id: string; app: string; title: string; params: Record<string, ParamSpec> }
type ProjectRow = { id: string; client: string; project: string; created: string; jobs: number; last_state: string | null }
type Preset = { id: string; name: string; color_mode: string; width: number; height: number; unit: string; status: string }
type Overview = {
  projects: ProjectRow[]
  operations: Operation[]
  presets: Preset[]
  preferences: { confirmed: number; waiting: number; unset: number; updated: string | null } | null
  handshake: HandshakeFile | null
  root: string
}
type Plan = {
  job_id: string
  op: string
  app: string
  title: string
  params: Record<string, unknown>
  inputs: { param: string; path: string; sha256: string; size: number; copy: string }[]
  outputs: string[]
  steps: string[]
  plan_sha256: string
  requested_by: string
}
type Review = { verdict: "approved" | "corrected"; note: string; by: string; at: string }
type Job = {
  job: string
  state: string
  approved_by: string | null
  error: string | null
  flags: string[]
  delivered: string[]
  preview: string | null
  notes: string[]
  replaced_by: string | null
  plan: Plan
  items: string[]
  reviews: Record<string, Review>
  todo: string[]
}
type ProjectView = { id: string; meta: { client: string; project: string }; brief: string; references: string[]; jobs: Job[]; files: Record<string, string[]> }
type Waiting = { section: string; id: string; status: string }
type Prefs = {
  profile: { settings: Record<string, { value: unknown; status: string; source: string }> } & Record<string, unknown>
  summary: { confirmed: number; waiting: number; unset: number }
  waiting: Waiting[]
  settings_help: Record<string, string>
}
type AppReport = { app?: string; ok?: boolean; installed_version?: string; error?: string; arabic?: Record<string, { arabic_ok?: boolean; reading?: string }>; [k: string]: unknown }
type HandshakeFile = { at?: string; apps?: Record<string, AppReport>; summary?: Record<string, string> }

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

const send = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
const detail = (d: unknown) => (d && typeof d === "object" && "detail" in d ? String((d as { detail: unknown }).detail) : "Something went wrong.")
const isImage = (p: string) => /\.(png|jpe?g|webp|gif|svg)$/i.test(p)

function StateBadge({ state }: { state: string }) {
  const v = state === "done" ? "default" : state === "failed" ? "destructive" : state === "approved" ? "secondary" : "outline"
  return <Badge variant={v}>{state}</Badge>
}

function LineRow(props: { label: string; value?: string; review?: Review; disabled?: boolean; onApprove: () => void; onCorrect: (note: string) => void; correctLabel?: string }) {
  const [open, setOpen] = React.useState(false)
  const [note, setNote] = React.useState("")
  return (
    <div className="border-b py-1.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 break-words text-sm">
          <span className="text-muted-foreground">{props.label}</span> {props.value}
        </span>
        {props.review?.verdict === "approved" && <Badge variant="secondary"><CheckIcon /> Approved</Badge>}
        {props.review?.verdict === "corrected" && <Badge>Corrected</Badge>}
        {!props.disabled && (
          <>
            <Button size="xs" variant="outline" onClick={props.onApprove}><CheckIcon /> Approve</Button>
            <Button size="xs" variant="ghost" onClick={() => setOpen(!open)}><PencilIcon /> {props.correctLabel ?? "Correct"}</Button>
          </>
        )}
      </div>
      {props.review?.note && <div className="pl-2 text-xs text-muted-foreground">Note: {props.review.note}</div>}
      {open && !props.disabled && (
        <div className="mt-1 flex gap-2">
          <Input className="h-8" placeholder="What is wrong? (required)" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button size="sm" disabled={!note.trim()} onClick={() => { props.onCorrect(note.trim()); setNote(""); setOpen(false) }}>Save</Button>
        </div>
      )}
    </div>
  )
}

function JobCard(props: { pid: string; job: Job; by: string; reload: () => void; setError: (m: string | null) => void }) {
  const { pid, job, by, reload, setError } = props
  const base = `/api/design/projects/${pid}/jobs/${job.job}`
  const open = job.state === "planned"
  const [busy, setBusy] = React.useState(false)
  const [newValue, setNewValue] = React.useState<Record<string, string>>({})
  const act = async (path: string, body?: unknown) => {
    setBusy(true)
    const r = await api<unknown>(`${base}/${path}`, send("POST", body ?? {}))
    setBusy(false)
    if (r.status !== 200) setError(detail(r.data))
    else setError(null)
    reload()
    return r
  }
  const review = (item: string, verdict: "approved" | "corrected", note = "") => act("review", { item, verdict, note, by })
  const replanParam = async (name: string) => {
    const raw = (newValue[name] ?? "").trim()
    if (!raw) return
    await act("replan", { changes: { [name]: raw }, by, note: "corrected on the page" })
  }
  type Line = { item: string; label: string; value: string; param?: string }
  const stepLines: Line[] = job.plan.steps.map((s, i) => ({ item: `step:${i + 1}`, label: `Step ${i + 1}:`, value: s }))
  const inputLines: Line[] = job.plan.inputs.map((i) => ({ item: `input:${i.param}`, label: "File:", value: `${i.path}  (SHA-256 ${i.sha256.slice(0, 12)}…, copy goes to ${i.copy})`, param: i.param }))
  const otherParams = Object.entries(job.plan.params).filter(([k]) => !job.plan.inputs.some((i) => i.param === k))
  const paramLines: Line[] = otherParams.map(([k, v]) => ({ item: `param:${k}`, label: `${k}:`, value: String(v), param: k }))
  const outLines: Line[] = job.plan.outputs.map((o) => ({ item: `output:${o}`, label: "Will make:", value: o }))
  return (
    <Card className={cn(job.state === "cancelled" && "opacity-60")}>
      <CardHeader className="flex flex-row flex-wrap items-center gap-2">
        <CardTitle className="text-base">{job.job}: {job.plan.title}</CardTitle>
        <StateBadge state={job.state} />
        <Badge variant="outline">{job.plan.app === "none" ? "no app" : job.plan.app}</Badge>
        {job.approved_by && <span className="text-xs text-muted-foreground">approved by {job.approved_by}</span>}
        {job.replaced_by && <span className="text-xs text-muted-foreground">replaced by {job.replaced_by}</span>}
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {[...stepLines, ...inputLines, ...paramLines, ...outLines].map((l) => (
          <div key={l.item}>
            <LineRow
              label={l.label}
              value={l.value}
              review={job.reviews[l.item]}
              disabled={!open}
              onApprove={() => review(l.item, "approved")}
              onCorrect={(note) => review(l.item, "corrected", note)}
            />
            {open && l.param !== undefined && (
              <div className="flex gap-2 py-1 pl-2">
                <Input className="h-8" placeholder={`New value for ${l.param} (makes a new plan)`} value={newValue[l.param] ?? ""} onChange={(e) => setNewValue({ ...newValue, [l.param as string]: e.target.value })} />
                <Button size="sm" variant="outline" disabled={busy || !(newValue[l.param] ?? "").trim()} onClick={() => replanParam(l.param as string)}>Re-plan</Button>
              </div>
            )}
          </div>
        ))}
        {job.error && <div className="rounded-md border border-destructive/40 p-2 text-sm text-destructive">{job.error}{job.flags.length > 0 && ` (${job.flags.join(", ")})`}</div>}
        {open && (
          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button size="sm" variant="outline" disabled={busy || job.todo.length === 0} onClick={async () => { for (const it of job.todo) await review(it, "approved") }}>
              Approve every line ({job.todo.length} left)
            </Button>
            <Button size="sm" disabled={busy || job.todo.length > 0 || !by.trim()} onClick={() => act("approve", { approved_by: by, plan_sha256: job.plan.plan_sha256 })}>
              <CheckIcon /> Yes, approve this plan
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => act("cancel")}><XIcon /> Cancel plan</Button>
            {!by.trim() && <span className="text-xs text-muted-foreground">Type your name above to give the yes.</span>}
          </div>
        )}
        {job.state === "approved" && (
          <div className="flex gap-2 pt-2">
            <Button size="sm" disabled={busy} onClick={() => act("run")}>{busy ? <Loader2Icon className="animate-spin" /> : <PlayIcon />} Run it</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => act("cancel")}><XIcon /> Cancel</Button>
          </div>
        )}
        {job.state === "done" && (
          <div className="flex flex-col gap-2 pt-2">
            <div className="text-sm font-medium">Delivered</div>
            {job.delivered.map((d) => (
              <div key={d} className="flex flex-col gap-1">
                <LineRow label="" value={d} review={job.reviews[`result:${d}`]} onApprove={() => review(`result:${d}`, "approved")} onCorrect={(n) => review(`result:${d}`, "corrected", n)} correctLabel="Needs a change" />
                {isImage(d) ? <img className="max-h-72 rounded-md border" src={`/api/design/projects/${pid}/file?rel=${encodeURIComponent(d)}`} alt={d} /> : <a className="pl-2 text-sm underline" href={`/api/design/projects/${pid}/file?rel=${encodeURIComponent(d)}`} target="_blank" rel="noreferrer">Open</a>}
              </div>
            ))}
            <div><Button size="sm" variant="outline" disabled={busy} onClick={() => act("undo")}><Undo2Icon /> Undo (remove what this job made)</Button></div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function Preferences({ by, setError }: { by: string; setError: (m: string | null) => void }) {
  const [prefs, setPrefs] = React.useState<Prefs | null>(null)
  const [vals, setVals] = React.useState<Record<string, string>>({})
  const load = React.useCallback(async () => {
    const r = await api<Prefs>("/api/design/preferences")
    if (r.status === 200) setPrefs(r.data)
  }, [])
  React.useEffect(() => void load(), [load])
  if (!prefs) return <div className="text-sm text-muted-foreground">Loading…</div>
  const confirm = async (section: string, id: string) => {
    const r = await api<unknown>("/api/design/preferences/confirm", send("POST", { section, id, approved_by: by }))
    setError(r.status === 200 ? null : detail(r.data))
    void load()
  }
  const correct = async (id: string) => {
    let value: unknown = (vals[id] ?? "").trim()
    try {
      value = JSON.parse(String(value))
    } catch {
      /* a plain word such as rtl stays text */
    }
    const r = await api<unknown>("/api/design/preferences/correct", send("POST", { id, value, approved_by: by }))
    setError(r.status === 200 ? null : detail(r.data))
    setVals({ ...vals, [id]: "" })
    void load()
  }
  const settings = prefs.profile.settings
  const tables = ["fonts", "document_presets", "export_presets", "palettes", "dos_donts", "reference_roots"]
  return (
    <div className="flex flex-col gap-4">
      <div className="text-sm text-muted-foreground">
        {prefs.summary.confirmed} confirmed by you, {prefs.summary.waiting} waiting for your answer, {prefs.summary.unset} not known yet. Entries read from your files or taken from the industry standard stay "draft" or "suggested" until you confirm or correct them.
      </div>
      <Card>
        <CardHeader><CardTitle className="text-base">Settings</CardTitle></CardHeader>
        <CardContent className="flex flex-col">
          {Object.entries(settings).map(([k, e]) => (
            <div key={k} className="flex flex-wrap items-center gap-2 border-b py-1.5 last:border-b-0">
              <span className="w-56 text-sm">{k}</span>
              <span className="w-48 break-words text-sm font-medium">{e.value === null ? "—" : JSON.stringify(e.value)}</span>
              <Badge variant={e.status === "confirmed" ? "secondary" : "outline"}>{e.status}</Badge>
              {e.value !== null && e.status !== "confirmed" && <Button size="xs" variant="outline" disabled={!by.trim()} onClick={() => confirm("settings", k)}><CheckIcon /> Confirm</Button>}
              <Input className="h-8 w-40" placeholder="Your value" value={vals[k] ?? ""} onChange={(ev) => setVals({ ...vals, [k]: ev.target.value })} />
              <Button size="xs" variant="ghost" disabled={!by.trim() || !(vals[k] ?? "").trim()} onClick={() => correct(k)}>Set</Button>
              <span className="w-full pl-2 text-xs text-muted-foreground">{prefs.settings_help[k]}. {e.source}</span>
            </div>
          ))}
        </CardContent>
      </Card>
      {tables.map((t) => {
        const rows = (prefs.profile[t] as { id: string; status: string; source: string; [k: string]: unknown }[]) ?? []
        return (
          <Card key={t}>
            <CardHeader><CardTitle className="text-base">{t.replace("_", " ")} ({rows.length})</CardTitle></CardHeader>
            <CardContent className="flex flex-col">
              {rows.map((r) => (
                <div key={r.id} className="flex flex-wrap items-center gap-2 border-b py-1 text-sm last:border-b-0">
                  <span className="min-w-0 flex-1 break-words">{String(r.family ?? r.name ?? r.text ?? r.path ?? r.id)}{r.seen_in_files ? ` (${String(r.seen_in_files)} files)` : ""}</span>
                  <Badge variant={r.status === "confirmed" ? "secondary" : "outline"}>{r.status}</Badge>
                  {r.status !== "confirmed" && <Button size="xs" variant="outline" disabled={!by.trim()} onClick={() => confirm(t, r.id)}><CheckIcon /> Confirm</Button>}
                </div>
              ))}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

type PipelineRow = { pipeline: string; state: string; title: string; stages: number; error: string | null; plan_sha256: string }
type PipelineStage = { id: string; op: string; title: string }
type PipelineView = {
  plan: { pipeline_id: string; plan_sha256: string; stages: PipelineStage[]; params: Record<string, unknown> }
  state: { state: string; error?: string | null; failed_stage?: string | null; approved_by?: string; stages?: Record<string, { ok: boolean; job?: string; delivered?: string[]; notes?: string[]; error?: string | null }> }
  markdown: string
}

function Pipelines(props: { pid: string; by: string; presets: Preset[]; setError: (m: string | null) => void }) {
  const { pid, by, presets, setError } = props
  const base = `/api/design/projects/${pid}/pipelines`
  const [rows, setRows] = React.useState<PipelineRow[]>([])
  const [view, setView] = React.useState<PipelineView | null>(null)
  const [busy, setBusy] = React.useState(false)
  const [open, setOpen] = React.useState(false)
  const [f, setF] = React.useState<Record<string, string>>({ layers: "4", quality: "draft", shape: "square", position: "top", size: "120", color: "#FFFFFF", motion: "none", seconds: "5", fps: "30" })
  const [apps, setApps] = React.useState<Record<string, boolean>>({ photoshop: true, illustrator: false, indesign: false })
  const set = (k: string, v: string) => setF((o) => ({ ...o, [k]: v }))
  const loadList = React.useCallback(async () => {
    const r = await api<{ pipelines: PipelineRow[] }>(base)
    if (r.status === 200) setRows(r.data.pipelines)
  }, [base])
  const loadOne = React.useCallback(
    async (id: string) => {
      const r = await api<PipelineView>(`${base}/${id}`)
      if (r.status === 200) setView(r.data)
      return r.status === 200 ? r.data : null
    },
    [base],
  )
  React.useEffect(() => {
    setView(null)
    void loadList()
  }, [loadList])
  const running = view?.state.state === "running"
  React.useEffect(() => {
    if (!view || !running) return
    const t = window.setInterval(() => void loadOne(view.plan.pipeline_id).then(() => loadList()), 3000)
    return () => window.clearInterval(t)
  }, [view, running, loadOne, loadList])
  const act = async (path: string, body: unknown) => {
    setBusy(true)
    const r = await api<PipelineView>(path, send("POST", body))
    setBusy(false)
    if (r.status !== 200) setError(detail(r.data))
    else {
      setError(null)
      setView(r.data)
    }
    void loadList()
    return r
  }
  const makePlan = async () => {
    const params: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(f)) {
      if (v.trim() === "") continue
      params[k] = ["layers", "size", "seconds", "seed"].includes(k) ? Number(v) : v.trim()
    }
    params.apps = Object.entries(apps).filter(([, on]) => on).map(([a]) => a).join(",")
    await act(base, { params })
  }
  const field = (k: string, label: string, w = "w-32", ph = "") => (
    <label className="flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Input className={`h-8 ${w}`} value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)} placeholder={ph} />
    </label>
  )
  const pick = (k: string, label: string, values: string[]) => (
    <label className="flex items-center gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <select className="h-8 rounded-md border bg-background px-2 text-sm" value={f[k] ?? ""} onChange={(e) => set(k, e.target.value)}>
        {values.map((v) => <option key={v} value={v}>{v}</option>)}
      </select>
    </label>
  )
  const st = view?.state
  return (
    <Card>
      <CardHeader className="cursor-pointer" onClick={() => setOpen(!open)}>
        <CardTitle className="text-base">Picture in layers from a description — the whole chain in one go {open ? "▾" : "▸"}</CardTitle>
      </CardHeader>
      {open && (
        <CardContent className="flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">
            Describe the picture (without any words in it: Arabic text is set afterwards with a real font). The chain makes it, splits it into transparent layers, names them, builds the layered file(s) with your title as live text, and
            can animate them in After Effects. You approve ONE plan with your name; each step still runs through the same safe, undoable operations and the chain stops at the first problem.
          </p>
          <Textarea value={f.prompt ?? ""} onChange={(e) => set("prompt", e.target.value)} placeholder="A shawarma wrap on a dark wooden table, warm light, empty dark space at the top" className="min-h-16" />
          <div className="flex flex-wrap gap-2">
            {field("name", "name", "w-40", "Hero Card")}
            <label className="flex items-center gap-1 text-sm">
              <span className="text-muted-foreground">preset</span>
              <select className="h-8 rounded-md border bg-background px-2 text-sm" value={f.preset ?? ""} onChange={(e) => set("preset", e.target.value)}>
                <option value="">pick a preset…</option>
                {presets.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.width}x{p.height} {p.unit})</option>)}
              </select>
            </label>
            {pick("layers", "layers", ["2", "3", "4", "5", "6", "7", "8"])}
            {pick("quality", "quality", ["draft", "standard"])}
            {pick("shape", "shape", ["square", "portrait", "landscape"])}
            {field("seed", "seed (optional)", "w-28")}
          </div>
          <div className="flex flex-wrap gap-2">
            {field("text", "title text (optional)", "w-56")}
            {pick("position", "position", ["top", "center", "bottom"])}
            {field("size", "size pt", "w-20")}
            {field("color", "colour", "w-24", "#F2C94C")}
            {field("font", "font (optional)", "w-36")}
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted-foreground">build in:</span>
            {Object.keys(apps).map((a) => (
              <label key={a} className="flex items-center gap-1">
                <input type="checkbox" checked={apps[a]} onChange={(e) => setApps((o) => ({ ...o, [a]: e.target.checked }))} /> {a}
              </label>
            ))}
            {pick("motion", "animate", ["none", "parallax", "zoom_in", "fade_in"])}
            {f.motion !== "none" && field("seconds", "seconds", "w-16")}
            {f.motion !== "none" && pick("fps", "fps", ["24", "25", "30"])}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={busy || !(f.prompt ?? "").trim() || !(f.name ?? "").trim() || !f.preset} onClick={makePlan}>Make the chain plan</Button>
            <select className="h-8 rounded-md border bg-background px-2 text-sm" value={view?.plan.pipeline_id ?? ""} onChange={(e) => e.target.value && void loadOne(e.target.value)}>
              <option value="">earlier chains…</option>
              {rows.map((r) => <option key={r.pipeline} value={r.pipeline}>{r.pipeline} — {r.title} — {r.state}</option>)}
            </select>
          </div>
          {view && st && (
            <div className="flex flex-col gap-2 rounded-md border p-2">
              <div className="flex items-center gap-2">
                <strong>{view.plan.pipeline_id}</strong> <StateBadge state={st.state} />
                {st.approved_by && <span className="text-xs text-muted-foreground">approved by {st.approved_by}</span>}
              </div>
              <pre className="whitespace-pre-wrap text-xs">{view.markdown}</pre>
              {st.error && <div className="text-sm text-destructive">{st.error}</div>}
              <ol className="list-decimal pl-5 text-sm">
                {view.plan.stages.map((s) => {
                  const r = st.stages?.[s.id]
                  return (
                    <li key={s.id}>
                      {s.title} {r ? (r.ok ? <Badge>done</Badge> : <Badge variant="destructive">failed</Badge>) : running ? <Loader2Icon className="inline size-3 animate-spin" /> : null}
                      {r?.delivered?.map((d) => <div key={d} className="break-all pl-2 text-xs text-muted-foreground">{d}</div>)}
                      {r?.notes?.map((n) => <div key={n} className="pl-2 text-xs">{n}</div>)}
                    </li>
                  )
                })}
              </ol>
              <div className="flex flex-wrap gap-2">
                {st.state === "planned" && (
                  <Button size="sm" disabled={busy || !by.trim()} onClick={() => act(`${base}/${view.plan.pipeline_id}/approve`, { approved_by: by, plan_sha256: view.plan.plan_sha256 })}>
                    <CheckIcon /> Yes, approve this chain (as {by.trim() || "…type your name above"})
                  </Button>
                )}
                {(st.state === "approved" || st.state === "failed") && (
                  <Button size="sm" disabled={busy} onClick={() => act(`${base}/${view.plan.pipeline_id}/run`, { resume: st.state === "failed" })}>
                    <PlayIcon /> {st.state === "failed" ? "Continue from the failed step" : "Run the chain"}
                  </Button>
                )}
                {(st.state === "done" || st.state === "failed") && (
                  <Button size="sm" variant="outline" disabled={busy} onClick={() => window.confirm("Undo every finished step of this chain (the files it delivered)?") && void act(`${base}/${view.plan.pipeline_id}/undo`, {})}>
                    <Undo2Icon /> Undo the chain
                  </Button>
                )}
              </div>
            </div>
          )}
        </CardContent>
      )}
    </Card>
  )
}

function Apps({ hs }: { hs: HandshakeFile | null }) {
  if (!hs || !hs.apps) return <div className="text-sm text-muted-foreground">No live handshake has been saved yet.</div>
  return (
    <div className="flex flex-col gap-3">
      <div className="text-xs text-muted-foreground">Last handshake: {hs.at ?? "unknown time"}</div>
      {Object.entries(hs.apps).map(([name, r]) => (
        <Card key={name}>
          <CardHeader className="flex flex-row items-center gap-2">
            <CardTitle className="text-base capitalize">{name}</CardTitle>
            <Badge variant={r.ok ? "secondary" : "destructive"}>{r.ok ? "answered" : "failed"}</Badge>
            {r.installed_version && <span className="text-xs text-muted-foreground">v{r.installed_version}</span>}
          </CardHeader>
          <CardContent className="flex flex-col gap-1 text-sm">
            {hs.summary?.[name] && <div>{hs.summary[name]}</div>}
            {r.error && <div className="text-destructive">{r.error}</div>}
            {Object.entries(r.arabic ?? {}).map(([k, a]) => (
              <div key={k} className="text-muted-foreground">Arabic ({k}): {a.arabic_ok ? "correct" : "not correct"}: {a.reading}</div>
            ))}
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export function DesignPage() {
  const [overview, setOverview] = React.useState<Overview | null>(null)
  const [pid, setPid] = React.useState<string>("")
  const [view, setView] = React.useState<ProjectView | null>(null)
  const [tab, setTab] = React.useState<"projects" | "preferences" | "apps">("projects")
  const [error, setError] = React.useState<string | null>(null)
  const [by, setBy] = React.useState<string>(() => {
    try {
      return localStorage.getItem("design_approver") ?? ""
    } catch {
      return ""
    }
  })
  const [client, setClient] = React.useState("")
  const [project, setProject] = React.useState("")
  const [brief, setBrief] = React.useState("")
  const [op, setOp] = React.useState("file.inspect")
  const [source, setSource] = React.useState("")
  const [params, setParams] = React.useState<Record<string, string>>({})
  const [uploading, setUploading] = React.useState(false)
  const fileInput = React.useRef<HTMLInputElement | null>(null)

  const loadOverview = React.useCallback(async () => {
    const r = await api<Overview>("/api/design/overview")
    if (r.status === 200) setOverview(r.data)
    return r.status === 200 ? r.data : null
  }, [])
  const loadProject = React.useCallback(async (id: string) => {
    if (!id) return setView(null)
    const r = await api<ProjectView>(`/api/design/projects/${id}`)
    if (r.status === 200) {
      setView(r.data)
      setBrief(r.data.brief)
    } else setView(null)
  }, [])
  const reload = React.useCallback(() => {
    void loadOverview()
    void loadProject(pid)
  }, [loadOverview, loadProject, pid])
  React.useEffect(() => {
    void loadOverview().then((o) => o && o.projects.length && !pid && setPid(o.projects[o.projects.length - 1].id))
  }, [loadOverview, pid])
  React.useEffect(() => void loadProject(pid), [loadProject, pid])
  const setName = (v: string) => {
    setBy(v)
    try {
      localStorage.setItem("design_approver", v)
    } catch {
      /* the name just is not remembered */
    }
  }

  const create = async () => {
    const r = await api<{ id: string }>("/api/design/projects", send("POST", { client, project }))
    if (r.status === 200) {
      setError(null)
      setClient("")
      setProject("")
      await loadOverview()
      setPid(r.data.id)
    } else setError(detail(r.data))
  }
  const saveBrief = async () => {
    const r = await api<unknown>(`/api/design/projects/${pid}/brief`, send("PUT", { text: brief }))
    setError(r.status === 200 ? null : detail(r.data))
  }
  const upload = async (files: FileList | null) => {
    if (!files || !pid) return
    setUploading(true)
    for (const f of Array.from(files)) {
      const r = await fetch(`/api/design/projects/${pid}/references/${encodeURIComponent(f.name)}`, { method: "PUT", body: f })
      if (!r.ok) {
        setError(`${f.name}: ${detail(await r.json().catch(() => null))}`)
        break
      }
    }
    setUploading(false)
    reload()
  }
  const plan = async () => {
    const spec = overview?.operations.find((o) => o.id === op)?.params ?? {}
    const sent: Record<string, unknown> = {}
    const fileName = Object.entries(spec).find(([, sp]) => sp.type === "file")?.[0]
    const filesName = Object.entries(spec).find(([, sp]) => sp.type === "files")?.[0]
    if (fileName) sent[fileName] = source.trim()
    if (filesName) sent[filesName] = source.split(/\r?\n/).map((x) => x.trim()).filter(Boolean)
    for (const [k, v] of Object.entries(params)) {
      const t = spec[k]?.type
      if (!(k in spec) || v.trim() === "") continue
      sent[k] = t === "int" || t === "float" ? Number(v) : t === "bool" ? v === "true" : v.trim()
    }
    const r = await api<unknown>(`/api/design/projects/${pid}/plan`, send("POST", { op, params: sent }))
    setError(r.status === 200 ? null : detail(r.data))
    reload()
  }
  const remove = async () => {
    const typed = window.prompt(`To delete this whole project folder type its id exactly:\n${pid}`)
    if (typed === null) return
    const r = await api<unknown>(`/api/design/projects/${pid}`, send("DELETE", { confirm: typed }))
    if (r.status === 200) {
      setPid("")
      setView(null)
      setError(null)
      void loadOverview()
    } else setError(detail(r.data))
  }
  const opParams = Object.values(overview?.operations.find((o) => o.id === op)?.params ?? {})
  const needsFile = opParams.some((sp) => sp.type === "file")
  const needsFiles = opParams.some((sp) => sp.type === "files")
  const refPath = (name: string) => `${overview?.root ?? ""}\\${pid}\\01_Brief\\${name}`

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-xl font-semibold">Design portal</h1>
        {overview?.preferences && <Badge variant="outline">Preferences: {overview.preferences.confirmed} confirmed, {overview.preferences.waiting} waiting</Badge>}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-sm text-muted-foreground">Your name</span>
          <Input className="h-8 w-40" value={by} onChange={(e) => setName(e.target.value)} placeholder="needed for every yes" />
          <Button size="icon-sm" variant="ghost" aria-label="Reload" onClick={reload}><RefreshCwIcon /></Button>
        </div>
      </div>
      <div className="flex gap-2">
        {(["projects", "preferences", "apps"] as const).map((t) => (
          <Button key={t} size="sm" variant={tab === t ? "default" : "outline"} onClick={() => setTab(t)}>{t === "apps" ? "Apps (live check)" : t[0].toUpperCase() + t.slice(1)}</Button>
        ))}
      </div>
      {error && <div className="rounded-md border border-destructive/40 p-2 text-sm text-destructive">{error}</div>}

      {tab === "preferences" && <Preferences by={by} setError={setError} />}
      {tab === "apps" && <Apps hs={overview?.handshake ?? null} />}

      {tab === "projects" && (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <div className="flex flex-col gap-3">
            <Card>
              <CardHeader><CardTitle className="text-base">New project</CardTitle></CardHeader>
              <CardContent className="flex flex-col gap-2">
                <Input placeholder="Client (English letters)" value={client} onChange={(e) => setClient(e.target.value)} />
                <Input placeholder="Project (English letters)" value={project} onChange={(e) => setProject(e.target.value)} />
                <Button size="sm" disabled={!client.trim() || !project.trim()} onClick={create}><FolderPlusIcon /> Create</Button>
                <span className="text-xs text-muted-foreground">Named by the studio standard: date_Client_Project, with numbered folders.</span>
              </CardContent>
            </Card>
            {(overview?.projects ?? []).slice().reverse().map((p) => (
              <button key={p.id} onClick={() => setPid(p.id)} className={cn("rounded-md border p-2 text-left text-sm", p.id === pid && "border-primary bg-muted/40")}>
                <div className="font-medium">{p.id}</div>
                <div className="text-xs text-muted-foreground">{p.jobs} job(s){p.last_state ? `, last: ${p.last_state}` : ""}</div>
              </button>
            ))}
          </div>
          <div className="flex flex-col gap-4">
            {!view && <div className="text-sm text-muted-foreground">Create a project or pick one on the left.</div>}
            {view && (
              <>
                <Card>
                  <CardHeader className="flex flex-row items-center gap-2">
                    <CardTitle className="text-base">Brief: {view.id}</CardTitle>
                    <Button className="ml-auto" size="xs" variant="ghost" onClick={remove}><Trash2Icon /> Delete project</Button>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-2">
                    <Textarea rows={4} value={brief} onChange={(e) => setBrief(e.target.value)} placeholder="What is it for, size, mood, text, deadline…" />
                    <div className="flex flex-wrap items-center gap-2">
                      <Button size="sm" onClick={saveBrief}>Save brief</Button>
                      <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => void upload(e.target.files)} />
                      <Button size="sm" variant="outline" disabled={uploading} onClick={() => fileInput.current?.click()}>{uploading ? <Loader2Icon className="animate-spin" /> : <UploadIcon />} Add reference files</Button>
                      <span className="text-xs text-muted-foreground">{view.references.length} reference file(s): {view.references.join(", ") || "none"}</span>
                    </div>
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader><CardTitle className="text-base">New plan</CardTitle></CardHeader>
                  <CardContent className="flex flex-col gap-2">
                    <div className="flex flex-wrap gap-2">
                      <select className="h-8 rounded-md border bg-background px-2 text-sm" value={op} onChange={(e) => { setOp(e.target.value); setParams({}) }}>
                        {(overview?.operations ?? []).map((o) => <option key={o.id} value={o.id}>{o.title}</option>)}
                      </select>
                      <select className="h-8 rounded-md border bg-background px-2 text-sm" value="" onChange={(e) => e.target.value && setSource(refPath(e.target.value))}>
                        <option value="">Pick a reference file…</option>
                        {view.references.map((r) => <option key={r} value={r}>{r}</option>)}
                      </select>
                    </div>
                    {needsFiles ? (
                      <textarea className="min-h-20 rounded-md border bg-background p-2 text-sm" value={source} onChange={(e) => setSource(e.target.value)} placeholder="One full file path per line, the bottom layer first (each is copied, never changed)" />
                    ) : needsFile ? (
                      <Input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Or the full path of any file on this computer (it is copied, never changed)" />
                    ) : null}
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(overview?.operations.find((o) => o.id === op)?.params ?? {})
                        .filter(([, spec]) => spec.type !== "file" && spec.type !== "files")
                        .map(([name, spec]) => (
                          <label key={name} className="flex items-center gap-1 text-sm">
                            <span className="text-muted-foreground">{name}{spec.required === false ? " (optional)" : ""}</span>
                            {name === "preset" ? (
                              <select className="h-8 rounded-md border bg-background px-2 text-sm" value={params[name] ?? ""} onChange={(e) => setParams({ ...params, [name]: e.target.value })}>
                                <option value="">pick a preset…</option>
                                {(overview?.presets ?? []).map((p) => (
                                  <option key={p.id} value={p.id}>{p.name} ({p.width}x{p.height} {p.unit}, {p.color_mode}{p.status !== "confirmed" ? ", draft" : ""})</option>
                                ))}
                              </select>
                            ) : spec.type === "enum" ? (
                              <select className="h-8 rounded-md border bg-background px-2 text-sm" value={params[name] ?? ""} onChange={(e) => setParams({ ...params, [name]: e.target.value })}>
                                <option value="">{spec.default !== undefined ? `default: ${String(spec.default)}` : "choose…"}</option>
                                {(spec.values ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
                              </select>
                            ) : (
                              <Input className={["prompt", "text", "names"].includes(name) ? "h-8 w-72" : "h-8 w-32"} value={params[name] ?? ""} onChange={(e) => setParams({ ...params, [name]: e.target.value })} placeholder={spec.min !== undefined ? `${spec.min} to ${spec.max}` : ""} />
                            )}
                          </label>
                        ))}
                    </div>
                    <div className="flex items-center gap-2">
                      <Button size="sm" disabled={(needsFile || needsFiles) && !source.trim()} onClick={plan}>Make the plan</Button>
                      <span className="text-xs text-muted-foreground">A plan only describes the work. Nothing runs until you check every line and say yes.</span>
                    </div>
                  </CardContent>
                </Card>
                <Pipelines pid={view.id} by={by} presets={overview?.presets ?? []} setError={setError} />
                {view.jobs.slice().reverse().map((j) => <JobCard key={j.job} pid={view.id} job={j} by={by} reload={reload} setError={setError} />)}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
