import * as React from "react"
import { Link } from "react-router-dom"
import { CircleIcon, MessageSquareIcon, WandSparklesIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { cn } from "@/lib/utils"

const OFFICE_APPS = [
  { id: "word", label: "Word" },
  { id: "excel", label: "Excel" },
  { id: "powerpoint", label: "PowerPoint" },
]

type ComfyState = "online" | "starting" | "offline"

export function StudioPage() {
  const [comfyState, setComfyState] = React.useState<ComfyState | null>(null)
  const [comfyElapsed, setComfyElapsed] = React.useState<number | null>(null)

  const [prompt, setPrompt] = React.useState("")
  const [steps, setSteps] = React.useState(30)
  const [cfg, setCfg] = React.useState(7)
  const [genStatus, setGenStatus] = React.useState<string | null>(null)
  const [genBusy, setGenBusy] = React.useState(false)
  const [gallery, setGallery] = React.useState<{ url: string; prompt: string }[]>([])

  const [officeApp, setOfficeApp] = React.useState(OFFICE_APPS[0].id)
  const [officeCmd, setOfficeCmd] = React.useState("")
  const [officeOut, setOfficeOut] = React.useState("Awaiting command...")
  const [officeBusy, setOfficeBusy] = React.useState(false)

  // The backend reports a tri-state (online / starting / offline) because SDXL
  // warm-up under VRAM pressure from the resident LLM + embedder can take
  // minutes, not seconds — a single fetch-on-mount would freeze on whatever
  // state happened to be true at page load. Poll so "starting" resolves to
  // "online" (or times out to "offline") without a page refresh. Poll faster
  // while a warm-up is actually in flight, slower once settled.
  React.useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function poll() {
      try {
        const res = await fetch("/api/comfy/status")
        const d = await res.json()
        if (cancelled) return
        const state: ComfyState = d.state ?? (d.running ? "online" : "offline")
        setComfyState(state)
        setComfyElapsed(typeof d.elapsed_s === "number" ? d.elapsed_s : null)
        timer = setTimeout(poll, state === "starting" ? 3000 : 15000)
      } catch {
        if (!cancelled) {
          setComfyState("offline")
          setComfyElapsed(null)
          timer = setTimeout(poll, 15000)
        }
      }
    }
    poll()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [])

  async function generateImage() {
    if (!prompt.trim()) return
    setGenBusy(true)
    setGenStatus("Queuing to ComfyUI — this may take 1-3 min on GPU...")
    try {
      const res = await fetch("/api/comfy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt, steps, cfg, width: 1024, height: 1024 }),
      })
      const data = await res.json()
      if (data.ok) {
        setGenStatus(`Done — ${data.filename}`)
        setGallery((prev) => [{ url: data.url, prompt }, ...prev])
      } else {
        setGenStatus(data.message ?? "Generation failed")
      }
    } catch (e) {
      setGenStatus(e instanceof Error ? `Network error: ${e.message}` : "Network error")
    } finally {
      setGenBusy(false)
    }
  }

  async function sendOfficeCmd() {
    if (!officeCmd.trim()) return
    setOfficeBusy(true)
    const cmd = officeCmd
    setOfficeCmd("")
    setOfficeOut(`Sending to ${officeApp}... (LLM generating spec, may take ~15s)`)
    try {
      const res = await fetch("/api/office", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: cmd, app: officeApp }),
      })
      const data = await res.json()
      if (data.ok) {
        setOfficeOut(`${data.message}${data.path ? `\nFile: ${data.path}` : ""}`)
      } else {
        setOfficeOut(data.message ?? "Failed")
      }
    } catch (e) {
      setOfficeOut(e instanceof Error ? `Network error: ${e.message}` : "Network error")
    } finally {
      setOfficeBusy(false)
    }
  }

  return (
    <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
      <h1 className="text-2xl font-semibold tracking-tight">Studio</h1>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {/* Image generation */}
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <CardTitle>Image Generation</CardTitle>
            <Badge variant="outline">ComfyUI / SDXL</Badge>
            {comfyState !== null && (
              <Badge
                variant={comfyState === "online" ? "default" : "secondary"}
                className="ml-auto"
              >
                <CircleIcon
                  className={cn(
                    "size-2",
                    comfyState === "online"
                      ? "fill-green-500"
                      : comfyState === "starting"
                        ? "fill-yellow-500 animate-pulse"
                        : "fill-muted-foreground",
                  )}
                />
                {comfyState === "online"
                  ? "Online"
                  : comfyState === "starting"
                    ? `Starting${comfyElapsed !== null ? ` (${comfyElapsed}s)` : "…"}`
                    : "Offline"}
              </Badge>
            )}
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Textarea
              placeholder="Describe the image..."
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>Steps</span>
                  <span>{steps}</span>
                </div>
                <input
                  type="range"
                  min={10}
                  max={50}
                  value={steps}
                  onChange={(e) => setSteps(Number(e.target.value))}
                  className="w-full"
                />
              </div>
              <div>
                <div className="mb-1 flex justify-between text-xs text-muted-foreground">
                  <span>CFG</span>
                  <span>{cfg.toFixed(1)}</span>
                </div>
                <input
                  type="range"
                  min={1}
                  max={15}
                  step={0.5}
                  value={cfg}
                  onChange={(e) => setCfg(Number(e.target.value))}
                  className="w-full"
                />
              </div>
            </div>
            <Button disabled={genBusy || !prompt.trim()} onClick={generateImage}>
              <WandSparklesIcon className="size-4" />
              Generate Image
            </Button>
            {genStatus && <p className="text-xs text-muted-foreground">{genStatus}</p>}
            {gallery.length > 0 && (
              <div className="grid grid-cols-3 gap-2">
                {gallery.map((g, i) => (
                  <a
                    key={i}
                    href={g.url}
                    target="_blank"
                    rel="noreferrer"
                    className="aspect-square overflow-hidden rounded-md border"
                    title={g.prompt}
                  >
                    <img src={g.url} alt={g.prompt} className="size-full object-cover" />
                  </a>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Creative Director */}
        <Card>
          <CardHeader>
            <CardTitle>Creative Director</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              Visual direction, creative briefs, and prompt ideation happen through the
              Creative Director agent chat.
            </p>
            <Button asChild className="self-start">
              <Link to="/agents?agent=creative_director">
                <MessageSquareIcon className="size-4" />
                Open Creative Director chat
              </Link>
            </Button>
          </CardContent>
        </Card>

        {/* Adobe bridge */}
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <CardTitle>Adobe Bridge</CardTitle>
            <Badge variant="outline" className="ml-auto">Retired</Badge>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <p className="text-sm text-muted-foreground">
              The typed-command box was retired. Adobe work now goes through the Design portal: fixed, tested
              operations, a plan first, and your yes before anything runs. The portal is being built piece by
              piece (preferences and the safe runner first).
            </p>
          </CardContent>
        </Card>

        {/* Office bridge */}
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <CardTitle>Office Bridge</CardTitle>
            <Badge variant="outline" className="ml-auto">COM</Badge>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Select value={officeApp} onValueChange={setOfficeApp}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {OFFICE_APPS.map((a) => (
                  <SelectItem key={a.id} value={a.id}>{a.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Office command (e.g. 'create a P&L template in Excel')..."
              rows={2}
              value={officeCmd}
              onChange={(e) => setOfficeCmd(e.target.value)}
            />
            <Button disabled={officeBusy || !officeCmd.trim()} onClick={sendOfficeCmd}>
              Run Command
            </Button>
            <pre className="min-h-10 rounded-md border bg-muted/40 p-2 text-xs whitespace-pre-wrap text-muted-foreground">
              {officeOut}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
