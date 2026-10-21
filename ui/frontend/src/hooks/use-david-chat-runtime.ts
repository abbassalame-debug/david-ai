import { useCallback, useEffect, useRef, useState } from "react"
import {
  useExternalStoreRuntime,
  CompositeAttachmentAdapter,
  SimpleImageAttachmentAdapter,
  type AttachmentAdapter,
  type AppendMessage,
  type ThreadMessageLike,
} from "@assistant-ui/react"

// Every non-image file (PDF, Office, data, audio, video, Adobe…) is uploaded to
// /api/upload the moment it's attached — videos are too big to base64 through
// the chat WebSocket. The returned server-side id is parked here, keyed by the
// composer's attachment id, and travels in the message as the "file" part's data.
const uploadIds = new Map<string, string>()

const uploadAttachmentAdapter: AttachmentAdapter = {
  accept: "*",
  async *add({ file }) {
    const base = {
      id: `${file.name}-${crypto.randomUUID()}`,
      type: "file" as const,
      name: file.name,
      contentType: file.type || undefined,
      file,
    }
    yield { ...base, status: { type: "running", reason: "uploading", progress: 0 } }
    try {
      const res = await fetch(`/api/upload?name=${encodeURIComponent(file.name)}`, {
        method: "PUT",
        body: file,
      })
      if (!res.ok) throw new Error(`Upload failed (${res.status})`)
      const { id } = (await res.json()) as { id: string }
      uploadIds.set(base.id, id)
      yield { ...base, status: { type: "requires-action", reason: "composer-send" } }
    } catch {
      yield { ...base, status: { type: "incomplete", reason: "error" } }
    }
  },
  async send(attachment) {
    const uploadId = uploadIds.get(attachment.id)
    if (!uploadId) throw new Error("Attachment upload did not complete")
    return {
      ...attachment,
      status: { type: "complete" },
      content: [
        {
          type: "file",
          data: uploadId,
          mimeType: attachment.contentType ?? "application/octet-stream",
          filename: attachment.name,
        },
      ],
    }
  },
  async remove(attachment) {
    uploadIds.delete(attachment.id)
  },
}

// Only formats the vision model can actually decode go down the image path;
// anything else image-like (tiff, svg…) falls through to the upload adapter.
const visionImageAdapter = new SimpleImageAttachmentAdapter()
visionImageAdapter.accept = "image/png,image/jpeg,image/webp,image/gif,image/bmp"

// Stateless — one instance covers every chat runtime on the page.
const imageAttachmentAdapter = new CompositeAttachmentAdapter([
  visionImageAdapter,
  uploadAttachmentAdapter,
])

// SimpleImageAttachmentAdapter.send() produces a full data URL
// ("data:image/png;base64,...."); Ollama's /api/chat "images" field wants
// just the raw base64 payload.
function stripDataUrlPrefix(dataUrl: string): string {
  const comma = dataUrl.indexOf(",")
  return comma === -1 ? dataUrl : dataUrl.slice(comma + 1)
}

type DavidMessage = ThreadMessageLike & { id: string }

// DavidMessage.content is always constructed as an array in this hook, but
// ThreadMessageLike's type also allows a bare string shorthand — narrow it
// explicitly so tsc -b (build mode, stricter than a bare `tsc --noEmit`
// check) doesn't reject .find()/.filter() on the union type.
function partsOf(message: DavidMessage) {
  return Array.isArray(message.content) ? message.content : []
}

function findPart(message: DavidMessage, type: "text" | "reasoning") {
  return partsOf(message).find(
    (p) => typeof p === "object" && "type" in p && p.type === type,
  ) as { text?: string } | undefined
}

function textOf(message: DavidMessage): string {
  return findPart(message, "text")?.text ?? ""
}

function reasoningOf(message: DavidMessage): string {
  return findPart(message, "reasoning")?.text ?? ""
}

function withText(message: DavidMessage, text: string): DavidMessage {
  const rest = partsOf(message).filter(
    (p) => !(typeof p === "object" && "type" in p && p.type === "text"),
  )
  return { ...message, content: [...rest, { type: "text", text }] }
}

// Reasoning part is only ever added when a real reasoning_token event arrives
// (deepseek-r1-class agents) — kept first so it renders above the final answer,
// and models that never think (llama3.1:8b etc.) never get an empty box.
function withReasoning(message: DavidMessage, text: string): DavidMessage {
  const rest = partsOf(message).filter(
    (p) => !(typeof p === "object" && "type" in p && p.type === "reasoning"),
  )
  return { ...message, content: [{ type: "reasoning", text }, ...rest] }
}

function nextId() {
  return crypto.randomUUID()
}

export function useDavidChatRuntime(options?: {
  agent?: string
  domain?: string
  forceAgent?: boolean
}) {
  const agent = options?.agent ?? "analyst"
  const domain = options?.domain ?? "general"
  const forceAgent = options?.forceAgent ?? false

  const [messages, setMessages] = useState<DavidMessage[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const assistantIdRef = useRef<string | null>(null)
  const threadFilesRef = useRef<Map<string, { name: string; isNew: boolean }>>(new Map())

  useEffect(() => {
    return () => {
      wsRef.current?.close()
    }
  }, [])

  const finishWithError = useCallback((errorText: string) => {
    const assistantId = assistantIdRef.current
    if (assistantId) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...withText(m, textOf(m) || errorText),
                status: { type: "incomplete", reason: "error", error: errorText },
              }
            : m,
        ),
      )
    }
    assistantIdRef.current = null
    setIsRunning(false)
  }, [])

  const onNew = useCallback(async (appended: AppendMessage) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.close()
    }

    const textPart = appended.content.find((p) => p.type === "text")
    const text = textPart && "text" in textPart ? textPart.text : ""

    // Only one image is sent to the backend per message (ws_chat's vision
    // path takes a single `image` field) — if more than one is attached,
    // the rest still render in the composed message but aren't analyzed.
    const imageAttachment = appended.attachments?.find((a) => a.type === "image")
    const imagePart = imageAttachment?.content.find((p) => p.type === "image")
    const imageDataUrl = imagePart && "image" in imagePart ? imagePart.image : undefined

    // Attachments stay in play for the rest of the thread: earlier files are re-sent
    // (new:false) so follow-up questions still see the document. The backend keeps a
    // cached extraction per upload, so this costs nothing after the first read.
    for (const a of appended.attachments ?? []) {
      const part = a.content.find((p) => p.type === "file")
      if (part && "data" in part) threadFilesRef.current.set(part.data, { name: a.name, isNew: true })
    }
    const filePayload = [...threadFilesRef.current].map(([id, f]) => ({ id, name: f.name, new: f.isNew }))
    for (const f of threadFilesRef.current.values()) f.isNew = false

    const userMessage: DavidMessage = {
      id: nextId(),
      role: "user",
      content: [{ type: "text", text }],
      attachments: appended.attachments,
    }
    const assistantId = nextId()
    const assistantMessage: DavidMessage = {
      id: assistantId,
      role: "assistant",
      content: [{ type: "text", text: "" }],
      status: { type: "running" },
    }
    assistantIdRef.current = assistantId
    setMessages((prev) => [...prev, userMessage, assistantMessage])
    setIsRunning(true)

    // Everything before this turn — the backend appends the last 10 of these
    // ({role, content} pairs) ahead of the current message, so it actually
    // knows what it's replying to instead of treating every message as a
    // fresh, context-free query.
    const historyPayload = messages.map((m) => ({
      role: m.role,
      content: textOf(m),
    }))

    const proto = location.protocol === "https:" ? "wss://" : "ws://"
    const ws = new WebSocket(`${proto}${location.host}/ws/chat`)
    wsRef.current = ws

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          message: text,
          agent,
          domain,
          history: historyPayload,
          image: imageDataUrl ? stripDataUrlPrefix(imageDataUrl) : null,
          files: filePayload,
          force_agent: forceAgent,
        }),
      )
    }

    ws.onmessage = (event) => {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }

      switch (data.type) {
        case "reasoning_token": {
          const token = typeof data.token === "string" ? data.token : ""
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? withReasoning(m, reasoningOf(m) + token) : m,
            ),
          )
          break
        }
        case "token": {
          const token = typeof data.token === "string" ? data.token : ""
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? withText(m, textOf(m) + token) : m,
            ),
          )
          break
        }
        case "image": {
          const url = typeof data.url === "string" ? data.url : ""
          const note = `\n\n[Image generated](${url})\n`
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? withText(m, textOf(m) + note) : m,
            ),
          )
          break
        }
        // Sent by the agentic ReAct loop (agent_loop.py) around each tool call.
        // Only tool_use renders — tool_result stays out of the transcript so raw
        // tool output (which can be arbitrarily large/unstructured) doesn't leak
        // into the message the way it briefly did on the Knowledge/Music pages.
        case "tool_use": {
          const tool = typeof data.tool === "string" ? data.tool : "a tool"
          const note = `\n\n*🔧 using \`${tool}\`…*\n\n`
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? withText(m, textOf(m) + note) : m,
            ),
          )
          break
        }
        case "done": {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId
                ? { ...m, status: { type: "complete", reason: "stop" } }
                : m,
            ),
          )
          if (assistantIdRef.current === assistantId) {
            assistantIdRef.current = null
            setIsRunning(false)
          }
          ws.close()
          break
        }
        case "error": {
          const errText = typeof data.error === "string" ? data.error : "Unknown error"
          if (assistantIdRef.current === assistantId) {
            finishWithError(errText)
          }
          ws.close()
          break
        }
        default:
          break
      }
    }

    ws.onerror = () => {
      if (assistantIdRef.current === assistantId) {
        finishWithError("WebSocket connection error.")
      }
    }

    ws.onclose = () => {
      if (assistantIdRef.current === assistantId) {
        finishWithError("Connection closed before the response finished.")
      }
    }
  }, [finishWithError, agent, domain, forceAgent, messages])

  const onCancel = useCallback(async () => {
    wsRef.current?.close()
    finishWithError("Cancelled.")
  }, [finishWithError])

  return useExternalStoreRuntime<DavidMessage>({
    messages,
    isRunning,
    setMessages: (next) => setMessages([...next]),
    onNew,
    onCancel,
    convertMessage: (m) => m,
    adapters: { attachments: imageAttachmentAdapter },
  })
}
