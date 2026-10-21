import * as React from "react"
import { Link } from "react-router-dom"
import { SearchIcon, MessageSquareIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type RagResult = {
  vault?: string
  file?: string
  text?: string
  [key: string]: unknown
}

export function MusicPage() {
  const [query, setQuery] = React.useState("")
  const [results, setResults] = React.useState<RagResult[]>([])
  const [error, setError] = React.useState<string | null>(null)
  const [loading, setLoading] = React.useState(false)
  const [searched, setSearched] = React.useState(false)

  async function doSearch() {
    const q = query.trim()
    if (!q) return
    setLoading(true)
    setError(null)
    setSearched(true)
    try {
      const res = await fetch("/api/rag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q }),
      })
      const data = await res.json()
      if (data.results?.length) {
        setResults(data.results.slice(0, 8))
      } else {
        setResults([])
        setError(data.error ?? "No results")
      }
    } catch {
      setError("RAG unavailable")
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 overflow-y-auto p-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Music</h1>
        <p className="text-sm text-muted-foreground">
          Catalog search runs against the RAG index. Everything else — production ideas,
          BPM/key/stem work, Ableton routing — happens through the Beat Architect chat.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Catalog Search</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div className="flex gap-2">
            <Input
              placeholder="Search the RAG index..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doSearch()}
            />
            <Button onClick={doSearch} disabled={loading}>
              <SearchIcon className="size-4" />
              Search
            </Button>
          </div>
          {loading && <p className="text-xs text-muted-foreground">Searching RAG index...</p>}
          {error && <p className="text-xs text-destructive">{error}</p>}
          {searched && !loading && !error && results.length === 0 && (
            <p className="text-xs text-muted-foreground">No results.</p>
          )}
          {results.length > 0 && (
            <ul className="flex flex-col gap-1">
              {results.map((r, i) => (
                <li key={i} className="rounded border px-2 py-1.5 text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{r.vault}/{r.file}</span>
                  {r.text ? `: ${r.text.slice(0, 160)}` : ""}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Beat Architect</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Track analysis, chord progressions, Ableton routing, and catalog discussion all go
            through the Beat Architect agent chat.
          </p>
          <Button asChild className="self-start">
            <Link to="/agents?agent=beat_architect">
              <MessageSquareIcon className="size-4" />
              Open Beat Architect chat
            </Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
