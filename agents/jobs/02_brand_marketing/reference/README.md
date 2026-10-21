# reference/

| File | What |
|---|---|
| `templates.json` | the registry of fillable templates (written from the real files, with their SHA-256 at registration) |

## Adding a template

1. Inspect it first: copy it to `<DAVID_DATA>\generated\brand\_inspect\` and list its text frames in Illustrator (live text or outlined
   curves? which fonts? what is the placeholder text?). Only live text can be filled.
2. Add an entry to `templates.json`: `source` (relative to `template_root`), `fields` with `anchor_text` (the exact placeholder
   text, which must be unique in the file), `script` (`arabic` if the font has Arabic letters, `latin` if not), `font`, `max_chars`.
3. Run `py -3.11 test_adobe_tools.py`, then a plan and a real fill on the copy, and look at the preview.

## Not ready (and why)

Brochures (43 unnamed frames), the branch poster (every frame twice), and Arabic certificates whose text is outlined. See `JOB.md`.
