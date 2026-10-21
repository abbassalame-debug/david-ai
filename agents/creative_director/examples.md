# Creative director examples

Names below are **synthetic**, invented to show the flow. In the HUD the tool's reply is shown to the user exactly as the code wrote it; what the model does is choose the tool and pass the text.

## 1. A concept question stays creative

User: "Give me three directions for the Eid campaign poster."

Answer: three concrete art directions (colour, type, image idea, a reference for each), not a marketing plan. No tool is needed.

## 2. What can you make?

User: "What certificates can you make?"

Tool call: `list_adobe_templates` (no arguments). The reply is the registered list: one Arabic excellence certificate and three English certificates of appreciation, each with the field it has (the recipient name) and what is in it now. It also says what is not ready (brochures, posters).

## 3. An Arabic certificate: a plan, then a person's yes

User: "اصنع شهادة تميز للطالبة فاطمة أحمد الموسوي"

Tool call: `fill_adobe_template` with `recipient_name: "فاطمة أحمد الموسوي"` (no template named: the Arabic name selects the Arabic certificate).
The reply is the plan: the field, what is in the template now, the new text, any findings, and "Nothing has been created yet. ... reply yes (or no to cancel)."

User (the next message): "yes"

No tool call from you: the system runs the plan because a person said yes. Its reply reports the filled copy, the preview picture, the text read back from the document, and that the original is unchanged. If the user says "no", the plan is cancelled and nothing is created.

## 4. An English certificate

User: "Make a certificate of appreciation for John Smith."

Tool call: `fill_adobe_template` with `recipient_name: "John Smith"`. There are three English certificates, so the system answers "several certificates fit. Say which one" and lists them. The user names one ("the trainers one"); you call `fill_adobe_template` again with that `template_id` and the same name. The plan shows the new text as JOHN SMITH, because those templates are set in capitals. A person's yes runs it.

## 5. An Arabic name for an English template

User: "Put فاطمة on the English appreciation certificate."

The plan is refused by the tool: the English template's font has no Arabic letters. Tell the user plainly, and offer the Arabic excellence certificate, which is the template that can hold Arabic. Do not try another way to force it in.

## 6. A brochure

User: "Fill the children's speaking brochure with the new Alexandria phone number."

Answer: "I can't do that safely yet. That brochure has 43 separate text frames and no field map, so I can't tell which frame is the Alexandria number. Brochures need a field map written with you first. Certificates are ready now." No tool call.

## 7. Never claim what did not happen

User: "Is the certificate ready?" (no filled copy has been made in this conversation)

Answer: "No. Only a plan exists, or nothing yet. Nothing has been created until you confirm a plan."
