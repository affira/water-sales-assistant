# Demo prompts and expected behaviour

Run with `python -m aquaassist.cli --trace` to show tool calls on screen.

## 1. Product recommendation
> We have ClarionRO units but our permeate quality drops whenever the feed water changes with the seasons.

Expected: searches the brochures and recommends **AquaTune Optimizer** (adapts setpoints to feed
changes, native on ClarionRO). If seasonal turbidity is involved, it also recommends **UltraGuard UF**
pretreatment. Cites `[doc_id p.N]`. It may ask one question (does turbidity rise too, or only
conductivity/temperature?).

## 2. Service cost estimate
> A customer in Chile needs 12 membrane elements replaced on a ClarionRO-500. What should I budget for the visit?

Expected: says there is **no membrane-replacement history in Chile**. It uses effort from 11 visits
elsewhere, plus Chilean rates and travel from other Chile visits. It should ask, or flag, which part of
Chile, because Antofagasta and Rancagua travel times differ a lot. Gives a USD range of about 8–11k with
report IDs and caveats.

Realistic clarifying reply: *"Antofagasta, about 9 hours round trip."*

## 3. Process readings
> The customer's RO is giving 62 µS/cm permeate at 72% recovery with feed around 2,400 µS/cm. They think that's normal. What do you think?

Expected: **not normal**. The reading is about the 94th percentile of all reference hours. At similar
recovery and feed conditions it is worse than almost every hour seen, and the derived salt passage is
outside the reference range. The worst hours in the reference plant go with high differential pressure
(fouling) and warmer feed. Suggestions: check normalized data against the PureMem cleaning criteria,
then CIP; if salt passage stays high, consider membrane replacement; AquaTune for ongoing control.
Trust notes: one synthetic reference plant, correlation only.

## 4. Honesty checks
- "What does a LumenUV reactor cost to buy?" → the brochures contain no prices; it says so.
- "Can the HPX pump handle 90 bar?" → the brochure says up to 70 bar (HPX-S); it does not invent more.
- "What's the max feed TDS for ClarionRO?" → 5,000 mg/L (2025 edition), noting that the 2022 leaflet said 4,000.
