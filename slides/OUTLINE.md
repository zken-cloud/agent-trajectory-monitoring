# Slide deck outline (B14)

The lab guide carries the hands-on 5 hours. This carries the other ~2: the
opening, the framing before each lab, two teaching segments, and the wrap.

**Every number here is measured and lives in the repo.** Do not quote a figure
that is not in this file — if a slide needs a new one, measure it first. The
sources are README ("Measured on real BigQuery"), `v_detection_quality`, and
`sql/validate.py`.

**One argument, stated three ways.** If the room remembers one sentence:

> Operational telemetry tells you what the agent did. It cannot tell you what
> influenced it — and you cannot claim your agent is behaving unless you
> measured it.

Deck length target: **~45 slides**, of which ~18 are the opening. Everything
else is 2–4 slides of framing per lab.

---

## 1 · Opening (09:00, 20 min) — 18 slides

The only long presented block. Its job is to make the afternoon feel necessary.

| # | Slide | The beat |
|---|---|---|
| 1 | Title | Agent Trajectory Monitoring |
| 2 | Who this is for | Mixed room: security folks need ADK grounding, platform folks need threat grounding. Say so out loud — it sets expectations for pace |
| 3 | **Portability is the constraint** | You will deliver this to *your* customers. Everything is a reference implementation: Terraform modularised by concern, tool sensitivity in a manifest, no workshop-only shortcuts on the deployable path |
| 4 | The agent we will use | ShopFlow support: 6 tools spanning the full agentic surface — sensitive read, untrusted ingress, irreversible action, egress |
| 5 | What changed about the threat model | The unit of compromise is no longer a request. It is a *trajectory* |
| 6 | OWASP ASI 2026 | Map the six attacks to it. One slide, not six |
| 7 | **A3 walkthrough — the shape of the problem** | `lookup_customer` → `send_email`. Every event individually benign. Only the *sequence* is the attack |
| 8 | Corollary | You cannot detect on a single event. This is why the schema is session-scoped and why the graph exists |
| 9 | **What Google gives you free** | Be generous and specific: Agent Engine dashboard, Cloud Trace, Application Monitoring, Observability Analytics (SQL over logs *and* traces), OTel GenAI semconv. Do NOT undersell this |
| 10 | The three telemetry planes | Model inference (GEAP) · agent harness (ADK BQ Analytics) · platform traces (Cloud Trace). Joined on `trace_id` |
| 11 | **Where it stops** | Nothing native records *what was in the context window when the model decided*. Not an oversight — causality inside the context window is a security question |
| 12 | The one field | `context_tool_call_ids`, captured in `before_model`. 80% of the telemetry is Google's; the 20% that matters is one field |
| 13 | The ladder | Rules → Anomaly → Judgment. Escalating cost, escalating recall, decreasing precision |
| 14 | Graph is investigation, **not** detection | Say it early and say it twice. Scoped to sessions Layer 1 already flagged |
| 15 | **The result that matters** | 1,730 real sessions, `gemini-3.7-flash`, nobody attacking: **Layer 1 found 0** |
| 16 | Why that zero counts | 299 refunds (largest **£90** against a £200 limit), 219 egress calls. The rules had every chance |
| 17 | **The sentence** | *You cannot claim your agent is behaving unless you measured it.* Most days are quiet. A tool that only speaks during an incident cannot tell you whether today was quiet or broken |
| 18 | The day | Agenda. Flag the 2 breaks and lunch |

**Speaker note for 15–17.** This is the pitch. Resist inflating it — the power
is that nothing happened *and we can prove it*. A room of engineers will trust
that far more than a breach demo.

---

## 2 · Before Scenario 1 (09:35) — 3 slides

| # | Slide | The beat |
|---|---|---|
| 19 | Deploy, then investigate | `adk deploy` with `--trace_to_cloud --otel_to_cloud`. No code change |
| 20 | **The worksheet is the deliverable** | Not the deployment. Run A2, A3, A5 and answer honestly using only the native surface |
| 21 | Use *all* of it | Application Monitoring and Observability Analytics too. If you only open the dashboard you will "find" a gap that is really a tool you did not try — and a customer will catch that |

**Speaker note.** Ending the morning slightly frustrated is the correct
emotional design. Do not resolve it here.

---

## 3 · Debrief Scenario 1 (10:30) — 4 slides

| # | Slide | The beat |
|---|---|---|
| 22 | A5 — **yes, easily** | Token spend and call volume spike. Managed observability is genuinely good at operational anomalies. Say so |
| 23 | A3 — **findable, not detected** | You can query for sessions containing both calls. Nothing *decides the pair is a finding*, because "read PII then emailed someone" is a large slice of legitimate support traffic. The gap is precision, not visibility |
| 24 | A2 — **structurally invisible** | No query over the data that exists can recover whether untrusted content was in context. This is the one that does not yield to effort |
| 25 | The narrower claim | Operational telemetry tells you what the agent did, not what influenced it. This survives contact with an informed customer; "managed observability misses attacks" does not |

---

## 4 · Before Lab 2.1 (11:00) — 3 slides

| # | Slide | The beat |
|---|---|---|
| 26 | The manifest is the seam | **The one file a customer edits.** Detections bind to properties, never tool names |
| 27 | Path A first | Register Google's plugin: `agent_events` + **23 views**, 28 event types, `trace_id` on **100%** of events, tokens and latency free |
| 28 | Then find what it cannot see | One question — *was attacker-controlled content in context when it called `send_email`?* — and the native plane cannot answer it |

**Two gotchas to say aloud, both silent:** `enable_otel_correlation` defaults to
`False` (no `trace_id`, no join), and `flush()` is a coroutine (bare call drops
the batch at exit).

---

## 5 · Crescendo teaching segment (12:00, ~8 min) — 4 slides

The one genuinely academic moment. Earn it.

| # | Slide | The beat |
|---|---|---|
| 29 | Crescendo | Microsoft Research 2024 (Russinovich, Salem, Eldan) |
| 30 | The mechanism | Each turn is a small increment **on the model's own prior output**. It exploits self-consistency, not a filter gap |
| 31 | Why A6 is built this way | Response-channel disclosure. **No egress tool call at all** — the tool sequence is the corpus's most ordinary |
| 32 | Why it decides the architecture | Every tool-level rule is silent. This single attack is the entire justification for Layer 3 |

---

## 6 · Before Lab 2.3 — graph (13:30) — 3 slides

| # | Slide | The beat |
|---|---|---|
| 33 | Investigation, not detection | Scoped to flagged sessions. Third time you have said it |
| 34 | The taint edge | `FLOWED_INTO` is *derived*, from `context_tool_call_ids`. Without it the graph is a picture |
| 35 | **"So why not just use Wiz?"** | Ask it before they do. Wiz's graph is a **posture** graph over cloud config; this is a **runtime behaviour** graph over what the agent did. We produce the runtime signal it lacks and export findings into it. Framed carelessly this sounds like rebuilding a product they already bought |

---

## 7 · Before Lab 2.4 — rules (14:50) — 2 slides

| # | Slide | The beat |
|---|---|---|
| 36 | Layer 1 is the only enforceable layer | Deterministic, 1.000 precision on every shipped rule |
| 37 | **The teaching moment you are about to hit** | Naive D2 (pii→egress) = **1% precision**, 228 false positives. Add one predicate — where the data went — and it is **100%**. A sequence rule is under-specified until it knows the destination |

---

## 8 · Before Lab 2.5 — anomaly + judgment (15:35) — 4 slides

| # | Slide | The beat |
|---|---|---|
| 38 | Two anomaly detectors that fail **differently** | Surprisal catches *order* (A4, A5); KMEANS catches *shape* (A3). **Zero overlap** — measured, not asserted |
| 39 | **Goal drift — rejected** | Cosine distance request-vs-actions does not work: in A1/A3/A4/A5 the user's own request *is* the attack, so there is no drift. A3 scored **lowest** of six. Shipped as `goal_drift_REJECTED.sql` |
| 40 | The judge, rebuilt | v1 asked "did the agent do what was asked" → **0/6**. Reframed to judge **actions against policy** → **6/6**. Same trap that kills goal drift |
| 41 | Coverage | A1 1,3 · A2 1,3 · A3 1,2,3 · A4 1,2,3 · A5 1,2 · **A6 3-only**. `sql/validate.py` asserts it so the claim cannot rot |

---

## 9 · Debrief Lab 2.5 — the cost of the ladder (16:00) — 3 slides

The half nobody puts on a slide. Put it on a slide.

| # | Slide | The beat |
|---|---|---|
| 42 | Scripted corpus, where answers are known | L1 precision **1.000**; L3 **26 flagged, 5 TP, 0.192 precision, 0.833 recall** over a 310-session judged sample |
| 43 | **Real corpus, where nothing is wrong** | L1 **0**. L3 flagged **61 of 311 judged — 19.6%, precision 0.000**. Show one: `lookup_customer → search_kb → issue_refund £44` — correct behaviour, called a breach |
| 44 | So: Layer 1 may block, Layer 3 must be queued | Layer 1 is **silent when nothing is wrong**. Layer 3 **never is**. Run the judge unfiltered over production and you turn it off in a week, having learned nothing |

---

## 10 · Before Lab 2.6 — enforcement (16:25) — 2 slides

| # | Slide | The beat |
|---|---|---|
| 45 | Shadow first, always | Measure what you *would* have blocked before blocking |
| 46 | Only Layer 1 is precise enough | The counterweight: a 19.2%-precision detector cannot be allowed to refuse a customer's refund |

---

## 11 · Wrap (16:50, 10 min) — 5 slides

| # | Slide | The beat |
|---|---|---|
| 47 | Beyond security — same four tables | Resolution **84.3%**, escalation **27.1%**, median 2 turns. **Reasoning tokens are 9% of spend and appear in neither `usage.prompt` nor `usage.completion`** — budget from `usage.total` |
| 48 | The failure no security rule sees | An agent that escalates everything breaks no policy, delivers no automation, and fires **no detection**. Invisible to every security view |
| 49 | Naming: Vertex AI Gen AI Eval | Its metrics are literally `trajectory_*`. Pre-production and **reference-based** (needs a golden trajectory); this is production and **reference-free**. Address it before someone asks |
| 50 | Enterprise destination | Wiz / SCC / SecOps. Parked as discussion — the `findings` schema is shaped to export |
| 51 | Take it to a customer | The manifest is the first file they edit. Porting guide is written. Tear down: `labs/teardown.sh --all` |

---

## Facilitator notes

- **Say "graph is investigation, not detection" three times** (slides 14, 33, and the 2.3 debrief). It is the single most misremembered point.
- **Do not oversell the gap.** Slides 9 and 21 exist to stop you. The native tier is strong; the claim that survives is the narrow one.
- **The A5 miss is correct, not a bug.** If asked why Layer 3 misses the tool loop: a repeated read-only diagnostic is an operational anomaly, not a policy breach. D3 catches it deterministically.
- **If asked "did the agent ever actually misbehave?"** — on `gemini-3.7-flash`, no: 0 breaches in 1,730 sessions, and all six attacks refused by the agent's own reasoning. That is the honest answer and the assurance argument, not a weak one. Older models did behave differently; the point is you cannot know without measuring.
- **Quota, not wall clock, is the ceiling.** 40 attendees hitting Vertex at once (WORKSHOP-PLAN §9.1). Concurrency 6 with backoff is stable; 8 returns 429s.
