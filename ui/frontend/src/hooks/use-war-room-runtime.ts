import { useCallback, useEffect, useRef, useState } from "react"

export type TurnStatus = "pending" | "streaming" | "done" | "error"

export type WarRoomTurn = {
  agent: string
  text: string
  status: TurnStatus
  error?: string
}

export type WarRoomState = {
  agents: string[]
  turns: WarRoomTurn[]
  synthesis: WarRoomTurn | null
  isRunning: boolean
  connectionError: string | null
}

const SYNTHESIS_AGENT = "synthesis"

function initialState(): WarRoomState {
  return {
    agents: [],
    turns: [],
    synthesis: null,
    isRunning: false,
    connectionError: null,
  }
}

export function useWarRoomRuntime(room: string) {
  const [state, setState] = useState<WarRoomState>(initialState)
  const wsRef = useRef<WebSocket | null>(null)
  // Bumped every time a new session starts; handlers close over their own
  // session's value and compare against sessionIdRef.current before applying
  // state updates, so a stale/late event from a superseded session can never
  // corrupt newer state (same technique as use-david-chat-runtime's assistantIdRef).
  const sessionIdRef = useRef(0)

  useEffect(() => {
    return () => {
      wsRef.current?.close()
    }
  }, [])

  // Reset transcript whenever the room identity changes so a stale session's
  // turns can't leak into the newly selected room's panel.
  useEffect(() => {
    wsRef.current?.close()
    sessionIdRef.current += 1
    setState(initialState())
  }, [room])

  const run = useCallback(
    (task: string) => {
      const text = task.trim()
      if (!text) return

      // Guard against concurrent sessions: close any live prior socket first.
      wsRef.current?.close()
      const sessionId = ++sessionIdRef.current

      setState({
        agents: [],
        turns: [],
        synthesis: null,
        isRunning: true,
        connectionError: null,
      })

      const proto = location.protocol === "https:" ? "wss://" : "ws://"
      const ws = new WebSocket(`${proto}${location.host}/ws/chat`)
      wsRef.current = ws

      const isCurrent = () => sessionIdRef.current === sessionId

      ws.onopen = () => {
        if (!isCurrent()) return
        ws.send(
          JSON.stringify({
            message: text,
            agent: "analyst",
            domain: "general",
            history: [],
            image: null,
            room,
          }),
        )
      }

      ws.onmessage = (event) => {
        if (!isCurrent()) return
        let data: Record<string, unknown>
        try {
          data = JSON.parse(event.data)
        } catch {
          return
        }

        switch (data.type) {
          case "room_start": {
            const agents = Array.isArray(data.agents)
              ? (data.agents as string[])
              : []
            setState((prev) => ({
              ...prev,
              agents,
              turns: agents.map((agent) => ({
                agent,
                text: "",
                status: "pending" as TurnStatus,
              })),
            }))
            break
          }
          case "agent_start": {
            const agent = typeof data.agent === "string" ? data.agent : ""
            setState((prev) => ({
              ...prev,
              turns: prev.turns.map((t) =>
                t.agent === agent ? { ...t, status: "streaming" } : t,
              ),
            }))
            break
          }
          case "token": {
            const agent = typeof data.agent === "string" ? data.agent : ""
            const token = typeof data.token === "string" ? data.token : ""
            setState((prev) => ({
              ...prev,
              turns: prev.turns.map((t) =>
                t.agent === agent
                  ? { ...t, text: t.text + token, status: "streaming" }
                  : t,
              ),
            }))
            break
          }
          case "agent_done": {
            const agent = typeof data.agent === "string" ? data.agent : ""
            const full = typeof data.full === "string" ? data.full : undefined
            setState((prev) => ({
              ...prev,
              turns: prev.turns.map((t) =>
                t.agent === agent
                  ? { ...t, text: full ?? t.text, status: "done" }
                  : t,
              ),
            }))
            break
          }
          case "agent_error": {
            const agent = typeof data.agent === "string" ? data.agent : ""
            const error = typeof data.error === "string" ? data.error : "Agent error"
            setState((prev) => ({
              ...prev,
              turns: prev.turns.map((t) =>
                t.agent === agent ? { ...t, status: "error", error } : t,
              ),
            }))
            break
          }
          case "synthesis_start": {
            setState((prev) => ({
              ...prev,
              synthesis: { agent: SYNTHESIS_AGENT, text: "", status: "streaming" },
            }))
            break
          }
          case "synthesis_token": {
            const token = typeof data.token === "string" ? data.token : ""
            setState((prev) =>
              prev.synthesis
                ? { ...prev, synthesis: { ...prev.synthesis, text: prev.synthesis.text + token } }
                : prev,
            )
            break
          }
          case "synthesis_error": {
            const error = typeof data.error === "string" ? data.error : "Synthesis error"
            setState((prev) =>
              prev.synthesis
                ? { ...prev, synthesis: { ...prev.synthesis, status: "error", error } }
                : prev,
            )
            break
          }
          case "room_done": {
            const synthesis = typeof data.synthesis === "string" ? data.synthesis : undefined
            setState((prev) => ({
              ...prev,
              synthesis: prev.synthesis
                ? { ...prev.synthesis, text: synthesis ?? prev.synthesis.text, status: "done" }
                : { agent: SYNTHESIS_AGENT, text: synthesis ?? "", status: "done" },
              isRunning: false,
            }))
            ws.close()
            break
          }
          default:
            break
        }
      }

      ws.onerror = () => {
        if (!isCurrent()) return
        setState((prev) => ({
          ...prev,
          isRunning: false,
          connectionError: "WebSocket connection error.",
        }))
      }

      ws.onclose = () => {
        if (!isCurrent()) return
        setState((prev) =>
          prev.isRunning
            ? {
                ...prev,
                isRunning: false,
                connectionError: "Connection closed before the session finished.",
              }
            : prev,
        )
      }
    },
    [room],
  )

  const cancel = useCallback(() => {
    wsRef.current?.close()
    sessionIdRef.current += 1
    setState((prev) => ({ ...prev, isRunning: false }))
  }, [])

  return { ...state, run, cancel }
}
