import * as React from "react"
import { useSearchParams } from "react-router-dom"
import { SearchIcon } from "lucide-react"

import { AssistantRuntimeProvider } from "@assistant-ui/react"
import { Thread } from "@/components/thread"
import { useDavidChatRuntime } from "@/hooks/use-david-chat-runtime"
import { AGENTS, AGENT_DOMAIN } from "@/lib/agents"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

function agentLabel(agent: string) {
  return agent
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1))
    .join(" ")
}

function AgentChat({ agent }: { agent: string }) {
  const runtime = useDavidChatRuntime({
    agent,
    domain: AGENT_DOMAIN[agent] ?? "general",
    forceAgent: true,
  })

  return (
    <div className="flex flex-1 flex-col overflow-hidden">
      <AssistantRuntimeProvider runtime={runtime}>
        <Thread />
      </AssistantRuntimeProvider>
    </div>
  )
}

export function AgentsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [query, setQuery] = React.useState("")

  const requestedAgent = searchParams.get("agent")
  const selectedAgent =
    requestedAgent && AGENTS.includes(requestedAgent) ? requestedAgent : AGENTS[0]

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return AGENTS
    return AGENTS.filter(
      (agent) =>
        agent.toLowerCase().includes(q) || agentLabel(agent).toLowerCase().includes(q),
    )
  }, [query])

  function selectAgent(agent: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set("agent", agent)
      return next
    })
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      <div className="flex w-72 shrink-0 flex-col border-r">
        <div className="p-2">
          <div className="relative">
            <SearchIcon className="pointer-events-none absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="Search agents..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              className="pl-8"
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-2 pt-0">
          {filtered.length === 0 ? (
            <p className="px-2 py-6 text-center text-sm text-muted-foreground">
              No agents found.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {filtered.map((agent) => {
                const isActive = agent === selectedAgent
                return (
                  <li key={agent}>
                    <button
                      type="button"
                      onClick={() => selectAgent(agent)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors",
                        isActive
                          ? "bg-muted text-foreground"
                          : "text-foreground/80 hover:bg-muted/60",
                      )}
                    >
                      <span className="truncate">{agentLabel(agent)}</span>
                      <Badge variant="outline" className="shrink-0">
                        {AGENT_DOMAIN[agent] ?? "general"}
                      </Badge>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      </div>
      <AgentChat key={selectedAgent} agent={selectedAgent} />
    </div>
  )
}
