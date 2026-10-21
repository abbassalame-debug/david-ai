import * as React from "react"
import { SearchIcon, RefreshCwIcon, FolderIcon, ZapIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const VAULTS = ["Business", "Music", "System", "Lore", "Research"]
const DOMAINS = ["business", "music", "system", "lore"]

type RagResult = {
  vault?: string
  file?: string
  text?: string
  [key: string]: unknown
}

type IngestJob = {
  pid: number
  cmd: string
  elapsed: number
  elapsed_fmt: string
}

export function KnowledgePage() {
  const [vault, setVault] = React.useState(VAULTS[0])
  const [files, setFiles] = React.useState<string[]>([])
  const [filesLoading, setFilesLoading] = React.useState(false)
  const [selectedFile, setSelectedFile] = React.useState<string | null>(null)
  const [noteContent, setNoteContent] = React.useState<string>(
    "Select a file from the vault browser to read it here.",
  )

  const [ragQuery, setRagQuery] = React.useState("")
  const [ragResults, setRagResults] = React.useState<RagResult[]>([])
  const [ragError, setRagError] = React.useState<string | null>(null)
  const [ragLoading, setRagLoading] = React.useState(false)

  const [jobs, setJobs] = React.useState<IngestJob[]>([]);
  const [chunkCount, setChunkCount] = React.useState<number | null>(null)
  const [ingestMsg, setIngestMsg] = React.useState<string | null>(null)
  const [customFolder, setCustomFolder] = React.useState("")
  const [customDomain, setCustomDomain] = React.useState(DOMAINS[0])
  const [busyKey, setBusyKey] = React.useState<string | null>(null)
  const mountedRef = React.useRef(true)

  const loadVault = React.useCallback(async (v: string) => {
    setVault(v)
    setFilesLoading(true)
    try {
      const res = await fetch(`/api/vault/${v}/list`)
      const data = await res.json()
      setFiles(data.files ?? [])
    } catch {
      setFiles([])
    } finally {
      setFilesLoading(false)
    }
  }, [])

  React.useEffect(() => {
    loadVault(VAULTS[0])
  }, [loadVault])

  async function readNote(path: string) {
    setSelectedFile(path)
    setNoteContent("Loading...")
    try {
      const res = await fetch(`/api/vault/${vault}/read?path=${encodeURIComponent(path)}`)
      const data = await res.json()
      setNoteContent(data.content ?? "")
    } catch {
      setNoteContent("Error reading note.")
    }
  }

  async function doRagSearch() {
    const q = ragQuery.trim()
    if (!q) return
    setRagLoading(true)
    setRagError(null)
    try {
      const res = await fetch("/api/rag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      })
      const data = await res.json()
      if (data.results?.length) {
        setRagResults(data.results.slice(0, 10))
      } else {
        setRagResults([])
        setRagError(data.error ?? "No results")
      }
    } catch {
      setRagError("RAG unavailable")
      setRagResults([])
    } finally {
      setRagLoading(false)
    }
  }

  // Ingest status/stats polling — both intervals cleaned up on unmount.
  const pollIngestStatus = React.useCallback(async () => {
    try {
      const res = await fetch("/api/ingest/status")
      const data = await res.json()
      setJobs(data.jobs ?? [])
    } catch {
      setJobs([])
    }
  }, [])

  const pollIngestStats = React.useCallback(async () => {
    try {
      const res = await fetch("/api/ingest/stats")
      const data = await res.json()
      setChunkCount(typeof data.count === "number" ? data.count : null)
    } catch {
      setChunkCount(null)
    }
  }, [])

  React.useEffect(() => {
    pollIngestStatus()
    pollIngestStats()
    const statusId = setInterval(pollIngestStatus, 8000)
    const statsId = setInterval(pollIngestStats, 30000)
    return () => {
      mountedRef.current = false
      clearInterval(statusId)
      clearInterval(statsId)
    }
  }, [pollIngestStatus, pollIngestStats])

  async function quickIngest(key: string, mode: string, folder: string, domain: string) {
    setBusyKey(key)
    setIngestMsg(null)
    try {
      const res = await fetch("/api/ingest/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, folder, domain }),
      })
      const data = await res.json()
      if (data.ok) {
        setIngestMsg(`Started — PID ${data.pid}`)
        setTimeout(() => { if (mountedRef.current) pollIngestStatus() }, 800)
      } else {
        setIngestMsg(data.message ?? "Failed to start")
      }
    } catch (e) {
      setIngestMsg(e instanceof Error ? e.message : "Failed to start")
    } finally {
      setBusyKey(null)
    }
  }

  async function browseFolder() {
    setBusyKey("browse")
    try {
      const res = await fetch("/api/browse-folder")
      const data = await res.json()
      if (data.ok && data.path) {
        setCustomFolder(data.path)
        setIngestMsg(`Folder selected: ${data.path}`)
      } else {
        setIngestMsg("No folder selected.")
      }
    } catch (e) {
      setIngestMsg(e instanceof Error ? e.message : "Browse failed")
    } finally {
      setBusyKey(null)
    }
  }

  async function startCustomIngest(audio: boolean) {
    const folder = customFolder.trim()
    if (!folder) {
      setIngestMsg("Select a folder first — click Browse or type a path.")
      return
    }
    const key = audio ? "audio" : "import"
    setBusyKey(key)
    try {
      const res = await fetch("/api/ingest/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "import", folder, domain: customDomain, analyze_audio: audio }),
      })
      const data = await res.json()
      if (data.ok) {
        setIngestMsg(`Started PID ${data.pid} — ${folder.split("\\").pop() || folder}`)
        setTimeout(() => { if (mountedRef.current) pollIngestStatus() }, 800)
      } else {
        setIngestMsg(data.message ?? "Failed to start")
      }
    } catch (e) {
      setIngestMsg(e instanceof Error ? e.message : "Failed to start")
    } finally {
      setBusyKey(null)
    }
  }

  async function killIngest(pid: number) {
    setBusyKey(`kill-${pid}`)
    try {
      const res = await fetch("/api/ingest/kill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid }),
      })
      const data = await res.json()
      setIngestMsg(data.ok ? `Killed PID ${pid}` : data.message)
      if (data.ok) setTimeout(() => { if (mountedRef.current) pollIngestStatus() }, 600)
    } catch (e) {
      setIngestMsg(e instanceof Error ? e.message : "Kill failed")
    } finally {
      setBusyKey(null)
    }
  }

  async function startTurbo() {
    setBusyKey("turbo")
    try {
      const res = await fetch("/api/ingest/turbo/start", { method: "POST" })
      const data = await res.json()
      setIngestMsg(data.ok ? `Turbo ingest started — PID ${data.pid}` : (data.error ?? "Failed"))
    } catch (e) {
      setIngestMsg(e instanceof Error ? e.message : "Failed")
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Vault browser */}
      <div className="flex w-64 shrink-0 flex-col border-r">
        <div className="p-2">
          <h2 className="px-2 pb-1 text-sm font-semibold text-muted-foreground">Vaults</h2>
          <div className="flex flex-col gap-1">
            {VAULTS.map((v) => (
              <Button
                key={v}
                variant={v === vault ? "secondary" : "ghost"}
                className="justify-start"
                size="sm"
                onClick={() => loadVault(v)}
              >
                <FolderIcon className="size-3.5" />
                {v}
              </Button>
            ))}
          </div>
        </div>
        <div className="flex-1 overflow-y-auto border-t p-2">
          {filesLoading ? (
            <p className="px-2 text-xs text-muted-foreground">Loading...</p>
          ) : files.length === 0 ? (
            <p className="px-2 text-xs text-muted-foreground">No files.</p>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {files.map((f) => (
                <li key={f}>
                  <button
                    type="button"
                    onClick={() => readNote(f)}
                    className={cn(
                      "w-full truncate rounded px-2 py-1 text-left text-xs",
                      selectedFile === f
                        ? "bg-muted text-foreground"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground",
                    )}
                    title={f}
                  >
                    {f}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {/* Center: search + note viewer */}
      <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
        <Card>
          <CardHeader>
            <CardTitle>RAG Search</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            <div className="flex gap-2">
              <Input
                placeholder="Search all vaults + knowledge facts..."
                value={ragQuery}
                onChange={(e) => setRagQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doRagSearch()}
              />
              <Button onClick={doRagSearch} disabled={ragLoading}>
                <SearchIcon className="size-4" />
                Search
              </Button>
            </div>
            {ragLoading && <p className="text-xs text-muted-foreground">Searching...</p>}
            {ragError && <p className="text-xs text-destructive">{ragError}</p>}
            {ragResults.length > 0 && (
              <ul className="flex max-h-48 flex-col gap-1 overflow-y-auto">
                {ragResults.map((r, i) => (
                  <li key={i} className="rounded border px-2 py-1 text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{r.vault}/{r.file}</span>
                    {r.text ? `: ${r.text.slice(0, 160)}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="flex flex-1 flex-col overflow-hidden">
          <CardHeader>
            <CardTitle className="truncate">{selectedFile ?? "Note Viewer"}</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 overflow-y-auto">
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-foreground">
              {noteContent}
            </pre>
          </CardContent>
        </Card>
      </div>

      {/* Right: ingest */}
      <div className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto border-l p-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Ingest</CardTitle>
            <div className="flex items-center gap-2">
              {jobs.length > 0 && <Badge variant="default">● running</Badge>}
              {chunkCount !== null && (
                <span className="text-xs text-muted-foreground">
                  {chunkCount.toLocaleString()} chunks
                </span>
              )}
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            {jobs.length > 0 && (
              <ul className="flex flex-col gap-1">
                {jobs.map((j) => (
                  <li key={j.pid} className="flex items-center justify-between gap-2 rounded border px-2 py-1">
                    <div className="min-w-0">
                      <p className="text-[10px] text-green-600">● active {j.elapsed_fmt}</p>
                      <p className="truncate text-xs" title={j.cmd}>{j.cmd}</p>
                    </div>
                    <Button size="sm" variant="destructive" onClick={() => killIngest(j.pid)}>
                      Kill
                    </Button>
                  </li>
                ))}
              </ul>
            )}

            <div className="grid grid-cols-2 gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busyKey === "all"}
                onClick={() => quickIngest("all", "ingest", "", "all")}
              >
                All Vaults
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busyKey === "business"}
                onClick={() => quickIngest("business", "import", "vault:business", "business")}
              >
                Business
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busyKey === "music"}
                onClick={() => quickIngest("music", "import", "vault:music", "music")}
              >
                Music
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busyKey === "system"}
                onClick={() => quickIngest("system", "import", "vault:system", "system")}
              >
                System
              </Button>
            </div>

            <div className="border-t pt-3">
              <p className="mb-1 text-[10px] tracking-wide text-muted-foreground uppercase">
                Custom Import
              </p>
              <Input
                placeholder="C:\path\to\folder"
                value={customFolder}
                onChange={(e) => setCustomFolder(e.target.value)}
                className="mb-2"
              />
              <div className="mb-2 grid grid-cols-2 gap-2">
                <Button size="sm" variant="outline" disabled={busyKey === "browse"} onClick={browseFolder}>
                  Browse
                </Button>
                <Select value={customDomain} onValueChange={setCustomDomain}>
                  <SelectTrigger size="sm" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {DOMAINS.map((d) => (
                      <SelectItem key={d} value={d}>{d}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Button
                  size="sm"
                  disabled={busyKey === "import"}
                  onClick={() => startCustomIngest(false)}
                >
                  Import Folder
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busyKey === "audio"}
                  onClick={() => startCustomIngest(true)}
                  title="Include BPM/key audio analysis"
                >
                  Audio+
                </Button>
              </div>
            </div>

            <div className="border-t pt-3">
              <Button
                className="w-full"
                variant="outline"
                disabled={busyKey === "turbo"}
                onClick={startTurbo}
              >
                <ZapIcon className="size-4" />
                Turbo GPU Ingest
              </Button>
              <p className="mt-1 text-[10px] text-muted-foreground">
                GPU batch · bge-m3 CUDA · stops Ollama temporarily.
              </p>
            </div>

            {ingestMsg && <p className="text-xs text-muted-foreground">{ingestMsg}</p>}
          </CardContent>
        </Card>

        <Button variant="ghost" size="sm" onClick={() => { pollIngestStatus(); pollIngestStats() }}>
          <RefreshCwIcon className="size-3.5" />
          Refresh ingest status
        </Button>
      </div>
    </div>
  )
}
