# tools.py — David agent tool registry (Ollama JSON schema format)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Search the web for current news, scores, prices, or any live information. Use when the user asks about recent events, today's news, or anything that needs an internet lookup.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query to send to DuckDuckGo"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": "Search the user's OWN previously-saved notes, business documents, product manuals, PDFs, or ingested project files via semantic search. Use when the answer might be in the user's personal/business library, including any question about what a named manual, guide, document or note says. NEVER use this for general knowledge, trivia, or facts you already know (capitals, science, common definitions, how to write standard code) — answer those directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The semantic search query"},
                    "agent":  {"type": "string", "description": "The agent context for relevance filtering (e.g. cfo, developer, analyst)", "default": "analyst"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_note",
            "description": "Read the full content of a specific note from an Obsidian vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vault": {"type": "string", "description": "Vault name: System, Business, Music, Lore, or Research"},
                    "path":  {"type": "string", "description": "Relative path to the note file inside the vault, e.g. 'CRM/interactions/2026-06-15.md'"}
                },
                "required": ["vault", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": "Write or append content to a NOTE FILE in an Obsidian vault — only when the user explicitly asks to save/record/persist something as a note. Do NOT use this to 'write' code, text, or any content that belongs in your chat answer instead — that applies to code too ('write a function/script' means put it in your answer, not here). Creates the file if it does not exist. Writing to an existing note is refused unless you set append=true (safe: adds to it) or overwrite=true (destructive: replaces it). Research is read-only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "vault":     {"type": "string", "description": "Vault name: System, Business, Music, or Lore (Research is read-only)"},
                    "path":      {"type": "string", "description": "Relative path to the note file, e.g. 'Ideas/my-idea.md'"},
                    "content":   {"type": "string", "description": "The content to write"},
                    "append":    {"type": "boolean", "description": "If true, append to the existing file (preferred for edits). Default false.", "default": False},
                    "overwrite": {"type": "boolean", "description": "Set true only to deliberately replace an existing note's entire contents. Required to overwrite; there is no version history.", "default": False}
                },
                "required": ["vault", "path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_calendar",
            "description": "Fetch today's Google Calendar events for Demo User.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_email",
            "description": "Fetch unread Gmail messages for Demo User.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {"type": "integer", "description": "Number of emails to fetch (default 5, max 10)", "default": 5}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_tasks",
            "description": "Get the current task ledger — all pending, running, and recently completed tasks.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task in the task ledger and assign it to a specific agent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":       {"type": "string", "description": "Short task title"},
                    "description": {"type": "string", "description": "Full task description — what the agent should do"},
                    "agent":       {"type": "string", "description": "Agent to assign: analyst, developer, cfo, executive_advisor, venture_architect, legal_counsel, creative_director, beat_architect, etc."}
                },
                "required": ["title", "description", "agent"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_war_room",
            "description": "Convene a war room — a multi-agent debate on a topic — and return the synthesized decision. Use for complex decisions that benefit from multiple expert perspectives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "room":    {"type": "string", "description": "Room name: executive-room, build-room, creative-studio, design-review-room, calibration-room, sound-story-room, render-room"},
                    "message": {"type": "string", "description": "The question or problem to present to the war room"}
                },
                "required": ["room", "message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delegate_to_agent",
            "description": "Send a prompt directly to a specific agent and return their response. Use when you need one expert's opinion without a full war room.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent":  {"type": "string", "description": "Agent name: analyst, developer, cfo, executive_advisor, venture_architect, legal_counsel, creative_director, beat_architect, linguist, etc."},
                    "prompt": {"type": "string", "description": "What to ask the agent"}
                },
                "required": ["agent", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_process",
            "description": "Start a David background process/daemon by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Process name: voice_daemon, voice_inbox, file_watcher, david_live, turbo_ingest, morning_briefing"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stop_process",
            "description": "Stop a running David background process by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Process name: voice_daemon, voice_inbox, file_watcher, david_live, turbo_ingest"}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "process_status",
            "description": "Check whether David background processes are running and get their PIDs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Process name to check, or 'all' to check every process", "default": "all"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Get current system metrics: CPU, RAM, GPU load, VRAM usage, GPU temperature.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": "Queue a text message for David to say aloud using XTTS-v2 voice synthesis.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to speak aloud"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": "Generate an image using Stable Diffusion XL via ComfyUI running on the local GPU.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Detailed image generation prompt"},
                    "steps":  {"type": "integer", "description": "Number of diffusion steps (default 20)", "default": 20}
                },
                "required": ["prompt"]
            }
        }
    },
]

# Job tools (restaurant operations, 2026-10-05). They are NOT in TOOLS on purpose: tools_for_domain() (every agent that has no
# agents/<agent>/role.md) never offers them, and agent_spec.tools_for_agent() offers one only to an agent whose role.md lists it
# in tools_allow. They are pure computation (restaurant_tools.py, menu_tools.py): no side effects, no network, no writes.
AGENT_ONLY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "reconcile_cash",
            "description": "Do the restaurant day's cash reconciliation EXACTLY: Expected = carried cash + POS sales - Wallet - expenses - transfer to admin; Variance = counted - expected; status clean, over or short. ALWAYS use this for any cash reconciliation, cash over/short or cash-count question; never do this arithmetic yourself. If the user gives USD notes and local currency (LBP) counted separately, pass counted_usd + counted_lbp + rate_lbp_per_usd and do NOT pass actual_cash_count. Pass ONLY the figures the user or the draft actually gave. If a figure was not given, leave it out: the tool reports exactly what is missing (status incomplete) and never guesses. Amounts are USD; counted_lbp is in local currency (LBP).",
            "parameters": {
                "type": "object",
                "properties": {
                    "carried_cash": {"type": "number", "description": "Cash carried from yesterday (float excluded), USD"},
                    "pos_sales": {"type": "number", "description": "POS sales for the day, USD (includes Wallet)"},
                    "wallet": {"type": "number", "description": "Wallet payments (in POS sales but paid electronically), USD. 0 if there were none"},
                    "expenses": {"type": "array", "description": "Cash expenses paid out today", "items": {"type": "object", "properties": {"label": {"type": "string"}, "amount": {"type": "number"}}, "required": ["amount"]}},
                    "expenses_total": {"type": "number", "description": "Total expenses in USD, only when the list is not available"},
                    "transfer_to_admin": {"type": "number", "description": "Cash transferred to administration, USD"},
                    "actual_cash_count": {"type": "number", "description": "ONE grand total of ALL cash counted tonight (registers + safe) in USD. Use it ONLY when a single grand total is stated; never put USD notes here when pounds are counted separately"},
                    "counted_usd": {"type": "number", "description": "USD notes counted (not a grand total)"},
                    "counted_lbp": {"type": "number", "description": "local-currency notes (LBP) counted"},
                    "rate_lbp_per_usd": {"type": "number", "description": "Exchange rate, L.L. per 1 USD (needed with counted_lbp)"},
                    "branch_rate_lbp_per_usd": {"type": "number", "description": "The branch setting for the rate, to detect a mismatch"},
                    "tolerance_usd": {"type": "number", "description": "Only if the manager set one; otherwise leave out"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_meat_variance",
            "description": "Check one meat's daily stock-ledger variance EXACTLY: raw used = starting + added - ending; cooked yield = raw used x 0.55; variance = POS sold + waste - cooked yield (positive = loss). ALWAYS use this for any meat variance or meat stock question; never do the arithmetic yourself. Weights are kilograms. Beef weights INCLUDE the fat/tallow (لية), not just the meat: when the parts are known pass them in starting_parts / added_parts / ending_parts and the tool adds all of them. Pass ONLY weights that were actually given; leave out anything missing and the tool reports it (status incomplete).",
            "parameters": {
                "type": "object",
                "properties": {
                    "meat": {"type": "string", "description": "chicken or beef"},
                    "starting_kg": {"type": "number", "description": "Raw stock at the start of the day"},
                    "added_kg": {"type": "number", "description": "Raw stock received during the day"},
                    "pos_sold_kg": {"type": "number", "description": "Cooked weight the POS says was sold"},
                    "ending_kg": {"type": "number", "description": "Raw stock weighed at the end of the day"},
                    "waste_kg": {"type": "number", "description": "Cooked weight wasted, if known"},
                    "cook_yield": {"type": "number", "description": "Only if it differs from the branch default 0.55; a fraction, not a percentage"},
                    "tolerance_pct": {"type": "number", "description": "Only if the manager set one; otherwise leave out"},
                    "starting_parts": {"type": "object", "description": "The starting weight as its parts when they are known. Beef: {meat, rich, tallow} in kg (the tallow, لية, is part of the beef weight; 0 if none). Chicken: {chicken, rich}"},
                    "added_parts": {"type": "object", "description": "The received weight as its parts (same keys as starting_parts)"},
                    "ending_parts": {"type": "object", "description": "The ending weight as its parts (same keys as starting_parts)"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "render_review_sheet",
            "description": "Build the standard review sheet for a restaurant draft: item lines with match status, BOTH readings wherever the two readings disagreed, a totals check, the cash and meat checks, and the questions for the manager. The counts, statuses and totals come from code and must not be edited. Give the draft_id of a saved draft package (preferred: never retype a package). It only shows matches it was given and never invents one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string", "description": "e.g. DRAFT-2026-09-20-001, a package saved by the pipeline"},
                    "package": {"type": "object", "description": "A draft package, only if there is no draft_id"},
                    "cash_result": {"type": "object", "description": "A reconcile_cash result to include"},
                    "meat_results": {"type": "object", "description": "check_meat_variance result(s) to include, e.g. {chicken: {...}, beef: {...}}"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_menu_glossary",
            "description": "Show the restaurant's menu as written on the staff price-and-menu cheat sheet (the definitive source): each item's Arabic and English name, category, price in USD, meat grams per protein, and what is unresolved (for example two kaizer formats with the same name). ALWAYS use this for any question about what is on the menu, an item's price, its Arabic or English name, its category or its grams; never answer those from memory. Optional filters: category, search, pos_only (only items that also exist in the app's menu).",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Only this category, e.g. chicken, beef, sojok, salad, offer, drinks, fries"},
                    "search": {"type": "string", "description": "Only items whose Arabic or English name contains this text"},
                    "pos_only": {"type": "boolean", "description": "true = only items that exist in the POS menu"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "match_menu_item",
            "description": "Find which menu item a printed or OCR-read item name is. ALWAYS use this to match an item name from a POS receipt or photo to the menu; never match names yourself and never guess. Pass the text exactly as it was read, without correcting its spelling, in raw_text (one name) or raw_texts (a list). It returns a score from code: 100 = exact after Arabic normalisation, 85-99 = suggested (the manager confirms), anything lower = needs a human. A name that is not on the menu is reported as not matched.",
            "parameters": {
                "type": "object",
                "properties": {
                    "raw_text": {"type": "string", "description": "One item name exactly as read from the receipt or photo"},
                    "raw_texts": {"type": "array", "items": {"type": "string"}, "description": "Several item names, each exactly as read"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_draft_package",
            "description": "Check a compiled restaurant draft package BEFORE the review sheet is rendered: it must fit the draft_package v0 schema, every matched item must really be in the menu glossary with a score the matching code agrees with, and the computed math must equal what the code computes from the package's own numbers. Give the draft_id of a saved package (preferred: never retype a package). Valid does not mean approved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string", "description": "e.g. DRAFT-2026-09-20-001, a package saved by the pipeline"},
                    "package": {"type": "object", "description": "A draft package, only if there is no draft_id"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_adobe_templates",
            "description": "Show which certificate templates exist. Use ONLY when the user asks what templates or certificates are available. Do NOT use it when the user asks to make, create or prepare a certificate for a person: use fill_adobe_template for that.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fill_adobe_template",
            "description": "Make a certificate for a named person from the institute's real Illustrator template. Use this whenever the user asks to make, create or prepare a certificate (an excellence certificate, a certificate of appreciation) for a person. Pass the person's name EXACTLY as the user wrote it in recipient_name; do not translate, shorten or correct it. You may leave template_id out: an Arabic name is put on the Arabic certificate, and for an English name the system asks which English certificate. This only makes a PLAN that is shown to the user: nothing is created and no application opens until the user answers yes in their next message. You cannot run the plan yourself.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_name": {"type": "string", "description": "The person's name, exactly as the user wrote it"},
                    "template_id": {"type": "string", "description": "Optional: a template_id such as cert_excellence_ar, cert_appreciation_en, cert_trainers_en, cert_branch_manager_en"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "export_adobe_preview",
            "description": "Export a new preview picture (PNG) of a file that David generated earlier (a filled copy). It can never be used on an original template.",
            "parameters": {
                "type": "object",
                "properties": {"file": {"type": "string", "description": "Path of a generated .ai file under <DAVID_HOME>\\generated\\brand"}},
                "required": ["file"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "review_adobe_preview",
            "description": "Have the vision model check an exported preview picture: Arabic letters joined and right-to-left, no missing glyphs, no clipped or overflowing text, the old placeholder gone and the new text present. Advisory only: it can say looks_ok, needs_changes or needs_a_human_look, never approved.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preview": {"type": "string", "description": "Path of a preview .png under <DAVID_HOME>\\generated\\brand"},
                    "new_text": {"type": "array", "items": {"type": "string"}, "description": "The text(s) that were put in, to check they appear"},
                    "old_text": {"type": "array", "items": {"type": "string"}, "description": "The placeholder text(s) that were replaced, to check they are gone"}
                },
                "required": ["preview"]
            }
        }
    },
]

ADOBE_TOOL_NAMES = {"list_adobe_templates", "fill_adobe_template", "export_adobe_preview", "review_adobe_preview"}

# Tools whose successful output must reach the user EXACTLY as the code produced it: the HUD sends it as the reply and no model
# rewords it (a model that re-types the review sheet changed its summary and dropped a section in the first live test).
MENU_TOOL_NAMES = {"get_menu_glossary", "match_menu_item", "validate_draft_package"}

VERBATIM_TOOL_NAMES = {"render_review_sheet", "reconcile_cash", "check_meat_variance", "list_adobe_templates", "fill_adobe_template",
                       "export_adobe_preview", "review_adobe_preview"} | MENU_TOOL_NAMES

# Quick lookup by name (every tool that exists, offered to everyone or only to some agents)
TOOL_NAMES = {t["function"]["name"] for t in TOOLS + AGENT_ONLY_TOOLS}

# ── Default tool-decision layer (2026-09-24) ──────────────────────────────────
# Closes the gap found live: the old _AGENTIC_PATTERN regex in server.py only
# let a message reach tool-calling if it happened to match one of a handful of
# hardcoded verb+noun phrasings — anything asked naturally got zero tool access
# and the model just guessed. Empirically verified (see session notes) that
# giving david-ft/qwen-ft the `tools` schema directly breaks their fine-tuned
# behavior (they hallucinate fake tool-shaped JSON even on tool-free questions,
# since their training data never included a tools context). Fix: always run a
# cheap tool-DECISION call on the base llama3.1:8b (the only model verified to
# use Ollama's native tool_calls format correctly — qwen2.5-coder:7b does not,
# tuned or not), then hand the final answer to the actual routed/fine-tuned
# model with NO tools schema at all, tool result folded in as plain context.
#
# TECHNICAL_DOMAIN_SKIPS_TOOLS: llama3.1:8b has a persistent, prompt-resistant
# bias toward calling SOME tool on any coding request that mentions files/
# scripts/folders (write_note, then query_knowledge, then speak — excluding
# one just shifted the false positive to the next). qwen-ft already writes
# correct code directly with no tool needed for the common case, so the
# technical domain (developer/uiux_engineer/prompt_architect) skips the
# tool-decision layer entirely rather than risk regressing code answers.
# 2026-09-29: swapped llama3.1:8b -> qwen3.5:9b after a 26-case suite against
# this exact TOOLS schema (all 15 tools, tool-free negatives) scored 25/26,
# with the one miss traced to an underspecified test prompt, not a real gap
# (retest with a fully-specified prompt: 3/3). Zero hallucinated tool calls
# on any negative case.
TOOL_DECISION_MODEL = "qwen3.5:9b"

TOOL_DECISION_SYSTEM = (
    "You are David's tool-decision layer. You have access to tools for real-time "
    "data, files, and actions. Call a tool ONLY when the request genuinely needs "
    "external information you don't already know, or needs to perform a real "
    "action/side effect. For anything you can answer directly from your own "
    "knowledge or reasoning (facts, math, writing, advice, code), do NOT call any "
    "tool — just say so with no tool call so the conversation can continue "
    "normally. A question about what a NAMED manual, guide, document, note, "
    "project or file says is about the user's own library: use query_knowledge for "
    "it, never search_web (search_web is only for live news, scores and prices)."
)

# 2026-10-03: the technical domain used to skip this layer entirely because
# llama3.1:8b falsely called write_note/query_knowledge/speak on coding requests.
# That reason is gone with qwen3.5, but a false positive on a SIDE-EFFECT tool
# (write_note, start_process, create_task, ...) would still be costly, and the skip
# left questions about David's own state ("is my file watcher running?" routes to
# the developer agent) answered by an ungrounded guess. So the technical domain now
# gets the READ-ONLY tools only: it can look things up, it can never change anything.
TECHNICAL_DOMAIN_SKIPS_TOOLS = {"technical"}
READ_ONLY_TOOL_NAMES = {
    "query_knowledge", "read_note", "get_calendar", "get_email", "get_tasks",
    "process_status", "get_metrics",
}


def tools_for_domain(domain: str):
    """Returns the tool list to offer for this domain. The technical domain gets the
    read-only subset (see TECHNICAL_DOMAIN_SKIPS_TOOLS above); everyone else, all tools."""
    if domain in TECHNICAL_DOMAIN_SKIPS_TOOLS:
        return [t for t in TOOLS if t["function"]["name"] in READ_ONLY_TOOL_NAMES]
    return TOOLS
