# reference/

Reference data the agents and the code may rely on. Keep it small, plain text or CSV, and free of credentials.

| File | What | State |
|---|---|---|
| `Demo_Bistro_Menu_Cheat_Sheet final.pdf` | the staff price and menu cheat sheet: Arabic and English names, prices, weights, and the "Kitchen Portion Reference" (internal recipe weights) | **The definitive source of the menu.** Its text layer is unreadable (broken font encoding). |
| `menu_cheat_sheet.json` | a transcription of the five pages of that PDF: 43 customer items, 28 kitchen portions, the combo upgrade. `get_menu_glossary` reads this file and refuses to load if the PDF's SHA-256 no longer matches the one recorded in it. | **Built.** If the PDF changes, the menu must be re-extracted before anything is matched. |
| `menu_aliases.json` | receipt spellings the sheet does not contain, each with its evidence. `proposed` entries are ignored; only `confirmed` ones are used. | **195 confirmed, 0 proposed** (and 18 unlisted items, X01-X18) (Phase 19 added the repeated misreads of the steak, combo, laban, Miranda and similar lines, and the double meal with a drink; the manager confirmed all 22 on 2026-10-06; then the 12-piece slider spellings, `سلايدر 6 دجاج+6 لحمه` = the Slider Mix C28 (2026-10-08), and `مياه معدنية`, `لبن عيران`, `وجبة موظف دجاج`). It also holds `unlisted_items`: ten things the POS prints that the sheet does not list, added by the manager with their meat grams: `X01` Diet Pepsi and `X03` Diet Seven Up ($1.10, no meat), `X02` staff meal with fries, `X04` to `X07` the tartar and garlic sauce tubs (no meat, no price: sometimes charged), `X08` Classic bread (no meat), `X09` the steak mix combo and `X10` the plain steak mix (65 g chicken + 65 g beef each, both confirmed by the manager). `pos_line_decisions` now also holds the ruling that `عرض 2 ساندويش كبير` is 260 g of chicken. The add-ons 'with beef sandwich' and 'with chicken sandwich' are the sheet's own entries (`إضافة ساندويش لحمة` / `إضافة ساندويش دجاج`, 100 g each) reached through aliases. It also holds `pos_line_decisions`: the manager's rulings on POS lines the sheet leaves ambiguous (the bare slider lines). **2026-10-15 (menu-change note, live database commits c15c7cc and 920d575):** `X16` offer 2 beef large (260 g beef, USD 12.00), `X17` offer 2 chicken regular combo with a drink (200 g chicken, USD 8.89) and `X18` the new no-drink double tortilla promotion (280 g chicken, USD 9.44), all named exactly as the POS prints them; the alias `عرض 2 دجاج عربي كبير` = K13 (the POS's new name for the 2 chicken large offer, 260 g chicken; the old name `عرض 2 ساندويش كبير` keeps matching for past days). The other renames (وجبة MIX, سلايدر 6 دجاج+6 لحمه, سلايدر دجاج 12حبه) were already aliases. The old double meal with a drink (C06) is unchanged. |
| `tolerances.md` | the limits the manager accepts for the cash difference and the meat variance | **Empty on purpose** (template below). The agents never invent a tolerance. |
| `branch_settings.md` | the current exchange rate, cook yield, float and holds, copied from the app's Settings | To add when the pipeline needs it. The app is the source of truth. |

## tolerances.md (template; set by the manager)

```
cash_difference_usd:  NOT SET     # report the difference, do not judge it
meat_variance_pct:    NOT SET
unusual_expense_usd:  NOT SET
```

## `ignored_line_rules` (menu_aliases.json)
Receipt lines the manager ruled are not items: `without` (starts with بدون), `extra` (starts with زيادة), `free` (contains مجاني or its misreading مجالي). Confirmed 2026-10-08. A fourth kind, `order option`, uses `equals` (the WHOLE normalised line must equal the phrase) plus `starts_with: [فقط]`: the lines the manager answered `ok` to in group 5 of the Sep 20 - Oct 5 review (only chicken, with potatoes, hot, ketchup, light sauce...). Words are compared after normalising. A rule with `status` other than `confirmed`, no kind or no word is ignored with a warning. The pipeline leaves these lines out of the item list and says so in a NOTE.

## `learning_log.json`
One line per learning run (`restaurant_learn.py`): date, what the aliases were learned from, how many were added or refused, and the alias total afterwards. The first entry (2026-10-08) is the 80 spellings the manager had confirmed by answering the Sep 1-13 and Sep 14-19 review lists.
