# contracts/

The files that move through the pipeline. They are written by `adobe_template_tools.py`; nothing here is written by a model.

| File | Where | What |
|---|---|---|
| `templates.json` (in `reference/`) | the registry | which templates may be filled, and each field's placeholder text, script (`arabic` / `latin`), font, `max_chars`, `digits`, `case` |
| plan record | `<DAVID_DATA>\generated\brand\plans\PLAN-<time>-<id>.json` | what a plan will do: template, fields (cleaned), the template's SHA-256, `used` flag. A plan runs once and expires after 30 minutes |
| run record | `<DAVID_DATA>\generated\brand\<time>-<template>\run.json` | what happened: the filled copy, the preview, the original's SHA-256 before and after (`original_unchanged`), the text read back, the findings |
| filled copy and preview | the same folder | `<template_id>.ai` (the filled COPY) and `<template_id>__preview.png` |

Rules: a template path never points into the generated folder; a generated file is never an original; nothing in these files
may hold a credential.
