// Source of truth: ui/server.py — WAR_ROOM_NAMES / ROOM_AGENTS (verified
// by direct read). Must stay identical to the backend dict — the client never
// invents its own agent roster, it only mirrors what the backend will actually run.
export type WarRoom = {
  id: string
  label: string
  agents: string[]
}

export const WAR_ROOMS: WarRoom[] = [
  {
    id: "executive-room",
    label: "Executive Room",
    agents: ["cfo", "executive_advisor", "venture_architect", "legal_counsel"],
  },
  {
    id: "build-room",
    label: "Build Room",
    agents: ["developer", "prompt_architect", "uiux_engineer"],
  },
  {
    id: "creative-studio",
    label: "Creative Studio",
    agents: ["creative_director", "brand_audience", "lore_keeper"],
  },
  {
    id: "design-review-room",
    label: "Design Review Room",
    agents: ["uiux_engineer", "creative_director"],
  },
  {
    id: "render-room",
    label: "Render Room",
    agents: ["prompt_architect"],
  },
  {
    id: "sound-story-room",
    label: "Sound Story Room",
    agents: ["beat_architect", "acoustician", "lore_keeper"],
  },
  {
    id: "calibration-room",
    label: "Calibration Room",
    agents: ["analyst", "performance_coach", "chief_of_staff"],
  },
]

export function agentLabel(agent: string): string {
  return agent
    .split("_")
    .map((w) => w[0]?.toUpperCase() + w.slice(1))
    .join(" ")
}
