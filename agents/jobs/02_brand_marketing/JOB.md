# Job 2: Brand and marketing assets (Academy business, Professionalism Foundation)

**Goal.** Produce print assets (first: certificates) from the academy's **existing** templates, with Arabic text that
comes out right, and with a person approving every result. David fills and checks. A person approves.

**Agents.** `creative_director` (finds the template, makes the plan, fills a copy) and `uiux_engineer` (reviews the exported
preview). The Adobe work itself is done by code (`adobe_template_tools.py`), not by a model writing scripts.

**Out of scope for now.** Brochures and posters (no field map yet); Photoshop, InDesign, After Effects, Premiere; final print
export (PDF with crop marks); sending, printing or publishing anything; designing from scratch.

## The pipeline

```
 Request ─► Creative Director ─► Export Preview ─► UI/UX Engineer ─► Human Approval
            plan, then fill a      PNG, automatic    vision review      the person decides
            COPY (after a yes)     in the fill step  of the picture
```

| # | Stage | Done by | Produces | Rule |
|---|-------|---------|----------|------|
| 1 | **Request** | the user: which certificate, for whom | a template and the field text | names are used exactly as written |
| 2 | **Creative Director fills the template** | `fill_adobe_template` makes a **plan** (no app, nothing created); the HUD runs it only after a person's own "yes" | the plan, then (after the yes) a filled **copy** | an original is never opened or changed; a model can never run a plan |
| 3 | **Export Preview** | the same step 2 (and `export_adobe_preview` to redo it) | a PNG next to the copy (longest side 1600 px) | only files David generated can be previewed |
| 4 | **UI/UX Engineer reviews** | `review_adobe_preview` (qwen3.5 vision) | verdict `looks_ok` / `needs_changes` / `needs_a_human_look` and eight checks | advice, never approval |
| 5 | **Human approval** | a person | approve, or ask for a change | nothing is final, sent or printed before this |

## Safety (enforced in code, tested)

- **Originals are never touched.** Python copies the template into a new folder under `<DAVID_DATA>\generated\brand\`; the
  application opens only the copy; the original's SHA-256 is checked before and after every run and reported.
- **Nothing is overwritten.** Each run gets a new folder; output names never reuse an existing file.
- **Preview first, and a human gate in code.** The model's tool can only make a PLAN; it has no way to run one (the `plan_id`
  argument is not in its schema and is dropped if sent). The HUD runs a plan only when the person's whole next message is a bare
  yes ("yes", "ok", "go ahead", "نعم", "تمام"...) right after the plan was shown, and cancels it on a bare no. Plans expire after 30
  minutes and run once.
- **Only registered templates, only registered fields.** A field is found by the template's own placeholder text; it must match
  exactly one frame, otherwise nothing is saved.
- **Exclusive tools.** `fill_adobe_template` belongs to `creative_director` alone; `review_adobe_preview` to `uiux_engineer`
  alone; list and export to both. No other agent can be given them, even by editing its role file.
- **The old keyword shortcut is gone** (`create a poster in photoshop` used to run an LLM-written script straight in the app).
- **The replies are code-built** and shown unchanged, so no model rewords a path, a name or a status.

## How Arabic text is handled

1. **In the template's own frame.** The tool replaces the *contents* of the existing text frame and never sets a font, direction,
   alignment or text engine. The frame keeps the template's Arabic font (for example Gulzar), size, centring and letter joining.
   Checked live on the real excellence certificate: the new name comes out joined, right to left, in the template's red and centred.
2. **Cleaned first.** Pre-shaped Arabic letter forms (pasted from PDFs) are converted to normal letters so the font can shape them;
   invisible text-direction override characters (which can reorder text) and zero-width spaces are removed; Arabic joiners are kept.
3. **Script rules.** Arabic letters are refused in a frame whose font is Latin-only (the English certificates), instead of showing
   empty boxes. Mixed Arabic and Latin text is allowed but flagged, because the order of the words must be checked in the preview.
4. **Digits** follow the field setting (keep, Western, or Arabic-Indic); Latin capitals follow the template (the English
   certificates are in capitals).
5. **Read back.** After the fill the text is read back from the document and compared with what was sent; the font after the fill is
   compared with the font before (a silent substitute would be flagged); a frame more than 25% wider than the placeholder is flagged.
6. **Looked at.** The preview is exported and checked by the vision model (joined letters, direction, missing glyphs, clipping...).
   This is advice: a person looks at the name on the picture.

## What exists and what does not

| Piece | State |
|---|---|
| Registered templates | **4 certificates**: `cert_excellence_ar` (Arabic), `cert_appreciation_en`, `cert_trainers_en`, `cert_branch_manager_en`. One field each: the recipient name |
| `list_adobe_templates`, `fill_adobe_template`, `export_adobe_preview`, `review_adobe_preview` | **Built, tested, run on the real files** in Illustrator 2026 |
| Role files for `creative_director` and `uiux_engineer`, shared rules | **Written** |
| Brochures (43 unnamed frames) and the branch poster (every frame twice) | **Not ready**: each needs a field map written with you |
| Other Arabic certificate files (students, board, thanks, flags) | **Not fillable**: their text is outlined into curves; they must be rebuilt with live text |
| Photoshop, InDesign, After Effects, Premiere | No template exists for them; not built |
| Final print export (PDF) | Not built; approval comes first |
| Studio page button (`/api/adobe`) | Still there: a manual button that creates new documents in a new file. It is not an agent path and was left alone |

## Known template defects (found by the first preview reviews)

- The English appreciation template's large title ("Certificate Of Appreciation") is wider than the page and is cut off at the edges in
  the exported picture. The review catches it. It is in the template, not in the fill.

## Open questions

1. Which brochure and poster should be made fillable first, and which text on them changes (branch name? phone number? date?)?
2. Should the Arabic certificates with outlined text be rebuilt with live text (so the other Arabic categories can be filled)?
3. Print export: PDF settings (bleed, crop marks, colour) and who signs off.
4. Illustrator must be installed with the template's fonts; fonts that come only from a cloud subscription are not checked.
