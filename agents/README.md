# agents/ — how David's agents are defined

This folder is the **source of truth for an agent's job**: who it is, what it may do, which tools it may use, and what a good answer looks like.
Every one of the 17 agents has a folder here.

## Layout

```
agents/
  README.md                      this file
  _shared/
    behavior.md                  rules every agent follows (language, no inventing, confirm before acting)
    safety.md                    hard limits (no live-database writes, never overwrite originals, the firewalled vault)
  <agent>/                       one folder per agent
    role.md                      header (id, tools) + the job description. LOADED ON EVERY TURN.
    examples.md                  worked examples. Loaded only when a pipeline asks for them (examples=True).
  jobs/
    <NN_job_name>/
      JOB.md                     the workflow: stages, who does what, where a human must approve
      contracts/                 the shapes of the files that move between stages
      reference/                 reference data the agents may rely on (synthetic in this repository)
      evals/                     synthetic golden cases for the job
```

The 17 agents: `chief_of_staff`, `developer`, `uiux_engineer`, `creative_director`, `brand_audience`, `beat_architect`, `cfo`, `executive_advisor`,
`venture_architect`, `legal_counsel`, `analyst`, `linguist`, `acoustician`, `language_specialist`, `lore_keeper`, `performance_coach`, `prompt_architect`.
The persona files are deliberately generic: edit them (or add your own) to describe the person and business you work for.

## How the prompt is built

`agent_spec.build_system_prompt(agent)` is the only place a system prompt is assembled. It is, in this order:

1. the body of `<agent>/role.md` (the header is not sent to the model)
2. the `background:` files named in the header, under a "background" heading
3. `_shared/behavior.md`
4. `_shared/safety.md`
5. `<agent>/examples.md`, **only** when called with `examples=True`

An unknown agent name falls back to the analyst. Callers add their own surface-specific tail (the date and chat formatting for the HUD, the war-room instructions,
the background-task suffix). Edits to these files take effect on the next message; no restart is needed.

## role.md header

A small block between `---` lines. One `key: value` per line, lists written `[a, b, c]`.

```
---
id: cfo                          must equal the folder name
tools_allow: [query_knowledge, read_note]    tools the agent may use TODAY (each must exist in tools.py)
tools_planned: [reconcile_cash]              tools this role needs but that are not built yet
background: [depth/cfo.md]       extra files appended to the prompt (paths relative to the repository root)
---
```

## Tool scoping

* An agent gets only the tools in `tools_allow` (and, in the technical domain, only if they are also read-only). The tool-decision layer offers only those tools,
  the executor refuses any other (and audits the refusal), and the agent loop follows the same list.
* Every name in `tools_allow` must exist in `tools.py`; every name in `tools_planned` must not. `test_agent_spec.py` enforces both, so a typo cannot silently
  remove a capability.
* Side-effect tools (`write_note`, `create_task`, `start_process`, `stop_process`, `generate_image`, `speak`, `run_war_room`, `delegate_to_agent`) are not in the
  restaurant agents' lists on purpose.
* A few tools are exclusive to named agents (`agent_spec.EXCLUSIVE_TOOL_OWNERS`), whatever a role file lists.

## Adding or changing an agent

1. Create `agents/<agent>/role.md` with a header and a body written as instructions to the agent.
2. List its tools. Prefer the smallest list that does the job.
3. Add `examples.md` (3 to 6 worked examples, labelled synthetic if the numbers are invented).
4. Add the agent to `AGENTS` in `david_router.py` and to `[agent_namespaces]` in `config/vaults.toml` (an agent that is not listed there searches no vault).
5. Run `python test_agent_spec.py`.
6. Keep secrets, keys and real customer data out of these files. They are read by local models.

## Boundary with other projects

The restaurant job meets an external application at one place only: the draft-package file described in `jobs/01_restaurant_ops/JOB.md` and `contracts/`.
David never holds that application's credentials and never writes to its database except through the human-approved upload step.
