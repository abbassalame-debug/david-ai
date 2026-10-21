// Source of truth: david_router.py — AGENTS list (verified by direct read).
export const AGENTS: string[] = [
  "chief_of_staff",
  "developer",
  "uiux_engineer",
  "creative_director",
  "brand_audience",
  "beat_architect",
  "cfo",
  "executive_advisor",
  "venture_architect",
  "legal_counsel",
  "analyst",
  "linguist",
  "acoustician",
  "language_specialist",
  "lore_keeper",
  "performance_coach",
  "prompt_architect",
]

// Domain groupings — must stay identical to AGENT_DOMAIN in ui/server.py, which
// itself mirrors david_router.py's Layer 1 classifier system prompt (the
// authoritative agent→domain assignments). chief_of_staff has no entry in the
// classifier prompt either; "general" here is a reasonable directory-page default,
// not a claim about backend routing.
export const AGENT_DOMAIN: Record<string, string> = {
  developer: "technical",
  uiux_engineer: "technical",
  prompt_architect: "technical",
  beat_architect: "music",
  acoustician: "music",
  executive_advisor: "business",
  cfo: "business",
  venture_architect: "business",
  legal_counsel: "business",
  brand_audience: "business",
  creative_director: "business",
  lore_keeper: "lore",
  linguist: "research",
  language_specialist: "research",
  performance_coach: "personal",
  analyst: "general",
  chief_of_staff: "general",
}
