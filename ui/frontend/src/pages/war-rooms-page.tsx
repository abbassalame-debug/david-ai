import * as React from "react"
import { useSearchParams } from "react-router-dom"
import { SendIcon, Loader2Icon, SparklesIcon } from "lucide-react"

import { useWarRoomRuntime, type WarRoomTurn } from "@/hooks/use-war-room-runtime"
import { WAR_ROOMS, agentLabel } from "@/lib/war-rooms"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"

function TurnSection({ turn, isSynthesis }: { turn: WarRoomTurn; isSynthesis?: boolean }) {
  const isStreaming = turn.status === "streaming"
  const isPending = turn.status === "pending"

  return (
    <section
      className={cn(
        "rounded-lg border p-4",
        isSynthesis
          ? "border-primary/40 bg-primary/5"
          : "border-border bg-card",
      )}
    >
      <div className="mb-2 flex items-center gap-2">
        {isSynthesis && <SparklesIcon className="size-4 text-primary" />}
        <h3
          className={cn(
            "text-sm font-semibold tracking-tight",
            isSynthesis && "text-primary",
          )}
        >
          {isSynthesis ? "Synthesis" : agentLabel(turn.agent)}
        </h3>
        {isStreaming && (
          <Loader2Icon className="size-3.5 animate-spin text-muted-foreground" />
        )}
        {turn.status === "error" && (
          <Badge variant="destructive">error</Badge>
        )}
      </div>
      {isPending ? (
        <p className="text-sm text-muted-foreground">Waiting…</p>
      ) : turn.status === "error" ? (
        <p className="text-sm text-destructive">{turn.error}</p>
      ) : (
        <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
          {turn.text}
          {isStreaming && (
            <span className="ml-0.5 inline-block h-3.5 w-1.5 animate-pulse bg-foreground/60 align-text-bottom" />
          )}
        </p>
      )}
    </section>
  )
}

function RoomSession({ room }: { room: string }) {
  const { agents, turns, synthesis, isRunning, connectionError, run } =
    useWarRoomRuntime(room)
  const [task, setTask] = React.useState("")

  const roomAgents = React.useMemo(
    () => WAR_ROOMS.find((r) => r.id === room)?.agents ?? [],
    [room],
  )

  function handleRun() {
    if (!task.trim() || isRunning) return
    run(task)
  }

  const hasTranscript = turns.length > 0 || synthesis !== null

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="border-b p-4">
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          {roomAgents.map((a) => (
            <Badge key={a} variant="outline">
              {agentLabel(a)}
            </Badge>
          ))}
          {agents.length > 0 && (
            <span className="text-xs text-muted-foreground">
              ({agents.length} agent{agents.length === 1 ? "" : "s"} in this session)
            </span>
          )}
        </div>
        <div className="flex items-start gap-2">
          <Textarea
            placeholder="Describe the task or question for this room..."
            value={task}
            onChange={(e) => setTask(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault()
                handleRun()
              }
            }}
            disabled={isRunning}
            className="min-h-16"
          />
          <Button
            onClick={handleRun}
            disabled={isRunning || !task.trim()}
            className="shrink-0"
          >
            {isRunning ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SendIcon className="size-4" />
            )}
            Run Session
          </Button>
        </div>
        {connectionError && (
          <p className="mt-2 text-sm text-destructive">{connectionError}</p>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!hasTranscript ? (
          <p className="py-12 text-center text-sm text-muted-foreground">
            Enter a task above and run a session to see this room's agents respond in turn.
          </p>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4">
            {turns.map((turn) => (
              <TurnSection key={turn.agent} turn={turn} />
            ))}
            {synthesis && <TurnSection turn={synthesis} isSynthesis />}
          </div>
        )}
      </div>
    </div>
  )
}

export function WarRoomsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const requestedRoom = searchParams.get("room")
  const selectedRoom =
    requestedRoom && WAR_ROOMS.some((r) => r.id === requestedRoom)
      ? requestedRoom
      : WAR_ROOMS[0].id

  function selectRoom(room: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set("room", room)
      return next
    })
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      <div className="flex w-72 shrink-0 flex-col border-r">
        <div className="p-3">
          <h2 className="text-sm font-semibold text-muted-foreground">War Rooms</h2>
        </div>
        <div className="flex-1 overflow-y-auto p-2 pt-0">
          <ul className="flex flex-col gap-1">
            {WAR_ROOMS.map((room) => {
              const isActive = room.id === selectedRoom
              return (
                <li key={room.id}>
                  <button
                    type="button"
                    onClick={() => selectRoom(room.id)}
                    className={cn(
                      "flex w-full flex-col gap-1.5 rounded-md px-2 py-2 text-left text-sm transition-colors",
                      isActive
                        ? "bg-muted text-foreground"
                        : "text-foreground/80 hover:bg-muted/60",
                    )}
                  >
                    <span className="font-medium">{room.label}</span>
                    <span className="flex flex-wrap gap-1">
                      {room.agents.map((a) => (
                        <Badge key={a} variant="outline" className="text-[10px]">
                          {agentLabel(a)}
                        </Badge>
                      ))}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
      <RoomSession key={selectedRoom} room={selectedRoom} />
    </div>
  )
}
