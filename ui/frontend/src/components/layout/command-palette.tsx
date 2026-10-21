import * as React from "react"
import { useNavigate } from "react-router-dom"

import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import { NAV_SECTIONS } from "@/lib/nav-sections"
import { AGENTS } from "@/lib/agents"

const MODES = [
  { id: "A", label: "Switch to Mode A — Daily Driver" },
  { id: "B", label: "Switch to Mode B — Deep Reasoning" },
  { id: "C", label: "Switch to Mode C — Studio" },
  { id: "D", label: "Switch to Mode D — Design Review" },
  { id: "E", label: "Switch to Mode E — Render" },
]

export function CommandPalette() {
  const [open, setOpen] = React.useState(false)
  const navigate = useNavigate()

  async function switchMode(modeId: string) {
    setOpen(false)
    try {
      await fetch(`/api/mode/${modeId}`, { method: "POST" })
    } catch {
      // Silently fail — the System page's mode badge is the source of truth
      // and will simply keep showing the previous mode on next poll.
    }
  }

  async function parkIdea() {
    setOpen(false)
    const text = window.prompt("What idea do you want to park?")
    if (!text || !text.trim()) return
    try {
      await fetch("/api/park-idea", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.trim() }),
      })
    } catch {
      // Best-effort — no persistent UI state to roll back.
    }
  }

  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "k" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault()
        setOpen((prev) => !prev)
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [])

  const runPageNav = (path: string) => {
    setOpen(false)
    navigate(path)
  }

  return (
    <CommandDialog open={open} onOpenChange={setOpen}>
      <CommandInput placeholder="Type a command or search..." />
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Pages">
          {NAV_SECTIONS.map((section) => (
            <CommandItem
              key={section.path}
              value={section.title}
              onSelect={() => runPageNav(section.path)}
            >
              <section.icon />
              <span>{section.title}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Agents">
          {AGENTS.map((agent) => (
            <CommandItem
              key={agent}
              value={agent}
              onSelect={() => {
                setOpen(false)
                navigate(`/agents?agent=${encodeURIComponent(agent)}`)
              }}
            >
              <span>{agent}</span>
            </CommandItem>
          ))}
        </CommandGroup>
        <CommandSeparator />
        <CommandGroup heading="Actions">
          {MODES.map((mode) => (
            <CommandItem
              key={mode.id}
              value={mode.label}
              onSelect={() => switchMode(mode.id)}
            >
              <span>{mode.label}</span>
            </CommandItem>
          ))}
          <CommandItem value="Park Idea" onSelect={parkIdea}>
            <span>Park Idea</span>
          </CommandItem>
        </CommandGroup>
      </CommandList>
    </CommandDialog>
  )
}
