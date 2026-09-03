# Agent Trajectory Monitoring — Full-Day Hands-On Workshop

**v1.0 — design plus a built, measured reference implementation.** All labs authored; every detection number in this document is measured, not estimated. See `README.md` for the results table and `LAB-GUIDE.md` for the attendee path.
Audience: mixed security + platform engineers, delivering to their own customers afterwards.
Duration: 7 hours contact time. Attendees bring their own Argolis project.

Outcome an attendee can claim: *"I can instrument an agent so I can answer, after the fact, whether it was attacked — I have working detections for six attack classes, and I can redeploy the whole pipeline into a customer environment from Terraform."*

---

## 0. Portability is a design constraint, not a nice-to-have

Attendees are delivering this to customers. That reframes what we're building: **the labs are not workshop scaffolding, they are a reference implementation that the workshop happens to teach.** Three consequences that bind the rest of this document:

- **No workshop-only shortcuts on the deployable path.** This is why Pub/Sub stays in (§4.1) rather than being demoted to a slide — a customer-grade telemetry pipeline needs a real streaming hop, and shipping something attendees would have to re-architect before using would waste the day.
- **Terraform is modularised by concern, not by lab** — `telemetry-pipeline/`, `graph-store/`, `detections/`, `agent-runtime/`. An attendee lifts the two modules their customer needs without unpicking lab sequencing.
- **Nothing hardcodes this agent.** Tool sensitivity lives in a config-driven manifest (`is_egress`, `is_mutating`, `sensitivity`, `required_scopes`); detections are written against those properties, never against tool names. The manifest is the first file a customer edits, and it should be the *only* file they must edit to get D1–D5 running.

Say this explicitly at 09:00. It changes how attendees take notes.

---

## 1. The design spine

Everything hangs off **one agent, attacked once, observed two ways.**

A single fictional agent — the *ShopFlow support agent* — carries all seven hours. Attendees deploy it to Agent Engine in the morning and to GKE after that. Same code, same tools, same attacks. The only thing that changes is how much we can see, and that difference *is* the curriculum.

This matters more than it sounds. The default failure mode for a 7-hour agent-security workshop is five disconnected labs where attendees spend the first fifteen minutes of each rebuilding context. A single continuous narrative means the threat model introduced at 09:00 is still the thing being detected at 16:30.

The pedagogical arc is a **detection ladder**, climbed once:

| Layer | The question it answers | Technique | Catches |
|---|---|---|---|
| **1 · Rules** | Did it break a rule I wrote? | Argument assertions + SQL window functions | A1–A5 |
| **2 · Anomaly** | Is this unlike normal? | Markov surprisal (order) + BQML `KMEANS` (shape) | A3, A4, A5 |
| **3 · Judgment** | Do these actions breach policy, whatever was asked? | LLM-as-judge over the whole trajectory | A1–A4, **A6** |

Measured coverage, not intended coverage — `sql/validate.py` asserts it:

| | A1 | A2 | A3 | A4 | A5 | **A6** |
|---|---|---|---|---|---|---|
| Layer 1 | ✓ | ✓ | ✓ | ✓ | ✓ | – |
| Layer 2 | – | – | ✓ | ✓ | ✓ | – |
| Layer 3 | ✓ | ✓ | ✓ | ✓ | – | **✓** |

**Three layers, not five.** An earlier draft split this into L0–L4. It collapsed for a reason worth stating in the room: five layers did not correspond to five distinct questions. Policy and sequence rules are both *deterministic SQL over trajectory data* — the only difference is whether the predicate looks at one row or a window, and that contrast lands better as two rules inside one layer than as two layers. Statistical and semantic anomaly both answer *"is this unlike normal?"*, differing only in representation. Merging each pair loses no idea and buys depth on what remains.

Each layer catches what the one below it misses, and each costs more. But the ordering is not a quality ranking, and the closing point of the day inverts the naive reading: **only Layer 1 is precise enough to enforce inline** (§4.6). The cheapest, least clever layer is the one that can actually stop an attack; the expensive layers can only tell you about it afterwards.

---

## 2. The shared assets

### 2.1 The victim agent

A customer-support agent with a deliberately realistic tool surface — every tool is individually reasonable and the *combination* is what's dangerous.

| Tool | Why it's in the threat model |
|---|---|
| `lookup_customer(email)` | Sensitive read. Returns PII from BigQuery. |
| `search_kb(query)` | **Untrusted content ingress.** KB docs are attacker-controllable → indirect prompt injection vector. |
| `issue_refund(order_id, amount)` | Irreversible, money-moving, has a policy limit. |
| `send_email(to, subject, body)` | **Egress sink.** The exfiltration channel. |
| `run_diagnostic(host)` | Command-flavoured; lateral-movement analogue. |
| `escalate_to_human(summary)` | Benign control — so "unusual" isn't trivially "any tool." |

That's the classic agentic attack surface in six tools: sensitive read, untrusted ingress, irreversible action, egress. Any attack path worth detecting is a walk through this set.

### 2.2 The attack suite

Mapped to OWASP Top 10 for Agentic Applications (ASI01–ASI10, Dec 2025) so attendees leave with vocabulary their customers' auditors recognise.

| # | Attack | OWASP | What the telemetry should show |
|---|---|---|---|
| A1 | Direct jailbreak / goal hijack in the user turn | ASI01 | CoT flips mid-invocation; tool chosen contradicts stated request |
| A2 | Indirect injection via poisoned KB doc | ASI01/ASI06 | `search_kb` result → next LLM call → unrequested `send_email` |
| A3 | PII exfiltration chain | ASI02 | `lookup_customer` → `send_email` to non-allowlisted domain |
| A4 | Policy evasion by splitting | ASI02 | 12 × `issue_refund($40)` instead of 1 × `$480` |
| A5 | Tool loop / cost DoS | ASI08 | Same tool+args ≥ *k* times, invocation never terminates |
| A6 | Multi-turn crescendo — **response-channel disclosure** | ASI01 | One benign `lookup_customer`, then PII disclosed across agent *replies*. **No egress tool call exists**, so every tool-level rule is silent by construction |

A1–A5 are caught by Layer 1. **A6 is caught by Layer 3 and nothing else** — that is why it is in the set, and it is the whole justification for the expensive layer.

That claim was wrong for most of the build. A6 originally ended in an email to an off-allowlist domain, which a one-line Layer 1 rule caught trivially, so Layer 3 was earning nothing. It is now response-channel disclosure with no egress call at all, and `sql/validate.py` **asserts** that A6 produces zero Layer 1 and zero Layer 2 findings. Claims about layer exclusivity rot silently unless a test holds them.

### 2.3 The corpus — tiered by consumer

**This is the highest-risk build item.** You cannot fit a Markov model or K-means on the traffic 20 attendees generate live. But Spanner and BigQuery need *different* things from the corpus, so it splits three ways:

| Tier | Volume | Store | Consumer |
|---|---|---|---|
| **T1 Statistical** | ~2,000 benign + labelled attacks | BigQuery | Layers 2–3 — Markov surprisal, BQML `KMEANS`, LLM judge |
| **T2 Graph** | ~300 sessions, **stratified** | Spanner Graph | Lab 2.3 |
| **T3 Attendee's own** | ~30 sessions | Both | Continuity — they see themselves in the data |

**Why T2 is small.** Graph labs fail from too *much* data, not too little. T1 in graph form is ~125k nodes / 200k edges — trivial for Spanner, unusable in a notebook visualisation. 300 sessions ≈ 7.5k nodes is explorable, and the taint query returns ~15–20 paths: enough to be real, few enough to inspect by hand.

**T2 is stratified, not randomly sampled.** All six attack classes must be present, and the benign half must deliberately include **near-misses** — sessions that legitimately read a KB doc and then send an email. The naive taint query *must* produce false positives, or attendees never learn to refine it with trust labels and approval checks. A random 300-of-2,000 sample gives boring benign traffic and a query that looks better than it is.

**T1 volume: 2,000, not 5,000.** `KMEANS` over ~6 features and a transition matrix over 7 tools converge well before 2k. Halve the generation cost and spend the saved effort on *diversity* — personas, intents, near-misses — because diversity determines whether detections separate and raw volume does not.

**Generate real, don't synthesise structurally.** ~2.2k sessions × ~6 LLM calls ≈ 13k calls on a fast cheap model at high concurrency: affordable in an afternoon. The tempting shortcut — generate 300 real and expand structurally — risks making *synthetic vs real* the dominant K-means split, which would quietly destroy Lab 2.5 in a way that's hard to notice until you're standing in front of the room.

**Validation gate, before any lab content is authored:** fit the K-means and transition model on T1; confirm the 200 attacks separate from benign; confirm no generator artefact (model version, timestamp banding, session-length quantisation) is a stronger signal than the attacks themselves. If the corpus doesn't separate, no amount of lab polish rescues the predictive lab.

---

## 3. Scenario 1 — Agent Engine, the managed path (1h10m)

Deploy ShopFlow to Vertex AI Agent Engine. Attendees get native **Cloud Trace** spans (ADK emits OpenTelemetry natively; `telemetry.googleapis.com` is the OTLP endpoint), **Agent Engine Sessions**, and the built-in **observability dashboard** — tokens, latency, error rates, tool calls over time.

Then they run A1, A3 and A5 against it and investigate using only native tooling.

**The lab's real purpose is the gap analysis.** A worksheet: for each attack, could you detect it, and with what? Honest answers:

- **A5 (loop / cost DoS) — yes.** The dashboard shows it plainly. Managed observability is genuinely good at operational anomalies.
- **A3 (exfil chain) — partially.** Every span is in Cloud Trace, but nothing joins "read PII" to "sent email" and calls it a finding. Visible only if you already knew to look.
- **A1 (goal hijack) — no.** Nothing evaluates whether the trajectory matched intent.

That gap — *operational observability is not security detection* — is the transition into the afternoon, and it lands far harder as something attendees discovered than as something a slide asserted. Ending the morning slightly frustrated is the correct emotional design.

---

## 4. Scenario 2 — GKE, building it ourselves (~4h50m)

### 4.1 Lab 2.1 — Telemetry (60 min)

Deploy the same agent to GKE, then instrument it. This lab has the widest audience spread (security folks need ADK grounding, platform folks need threat grounding), so it's structured as *plugin gets you most of the way, callbacks get you the security-relevant rest*.

**Path A — the ADK BigQuery Agent Analytics plugin.** BUILT and registered in `harness/run.py`. Captures execution to BigQuery via the Storage Write API, batched async, auto-creating an `agent_events` table and **23** per-event-type views (`v_llm_request`, `v_llm_response`, `v_tool_starting`, `v_tool_completed`, `v_agent_starting`, …) across 28 event types, with identity headers (`session_id`, `invocation_id`, `user_id`, `trace_id`) already unnested. Measured live: `trace_id` populated on **100%** of events. It also records **tool-call origin** — local function vs MCP server vs sub-agent vs A2A remote — which is a detection signal in its own right, and which supersedes the `origin` field we used to set by hand.

Roughly 80% of the required telemetry from a plugin registration. That's deliberate: it buys time for the 20% that carries the security value.

Two gotchas, both silent: `enable_otel_correlation` **defaults to `False`** (leave it off and there is no `trace_id`, so nothing joins to Cloud Trace), and `flush()` is a **coroutine** — calling it bare drops the batch at process exit.

**Path B — ADK callbacks, the security-relevant 20%.** This is the part to teach slowly and the part attendees will reuse at customers. Each callback maps to one of the four telemetry classes in the brief:

| Callback | Captures | Telemetry class |
|---|---|---|
| `before_model` | Prompt composition; **which tool results are in context** | Enables the taint edge in §4.3 |
| `after_model` | Structured **reasoning summary**, finish reason | Model CoT |
| `before_tool` | **Decision record** — tool, args, model's stated justification | Tool call decision |
| `after_tool` | Outcome, error class, **data-sensitivity tag** from what was touched | Tool call outcome |
| session end | Resolved / escalated / abandoned + goal-satisfaction verdict | Multi-turn outcome |

`before_model` is the non-obvious one and worth calling out: capturing *which prior tool results were in the context window* is what makes taint tracking possible in Lab 2.3. Skip it and the graph lab degrades to a picture.

**Design point worth 10 minutes.** Don't log raw chain-of-thought verbatim into a security table. It's high-volume, routinely contains the PII the agent just read, and creates a compliance problem inside the tool meant to solve one. Log a structured summary plus a hash; keep raw CoT in a short-retention bucket with separate IAM. Attendees will hit this at customers.

**Streaming path — kept, and deployed by Terraform.** ADK plugin → Pub/Sub → BigQuery. Attendees don't hand-build it; they `terraform apply` the `telemetry-pipeline/` module and inspect what it created. Rationale is §0: a customer-grade pipeline needs a real streaming hop for sub-minute detection latency, and shipping something they'd have to re-architect defeats the purpose. Cost is ~10 minutes of lab time, spent reading IaC rather than clicking consoles — which is the right thing for this audience anyway.

### 4.2 Lab 2.2 — Red team (45 min)

Run all six attacks. Then — before touching any detection tooling — **manually investigate one attack trajectory in raw BigQuery**, by hand, with SQL.

This is intentionally tedious. Attendees need to feel the join pain of reconstructing a trajectory from flat event rows, because that pain is the motivation for the next lab. Fifteen minutes of self-joins to work out which LLM call caused which tool call is the best possible setup for a graph database.

### 4.3 Lab 2.3 — The trajectory graph (75 min)

Wiz's graph works because it models *entities and relationships*, then queries for **paths** — "internet-exposed VM → has vuln → assumes role → reads secret bucket." The agent analogue is direct, and this is the intellectual centrepiece of the day.

**Store: Spanner Graph** (GA, ISO GQL). Visualisation via the open-source `spanner-graph-notebook`. Note for the room: Spanner Graph is `CREATE PROPERTY GRAPH` over ordinary relational tables, so the BQ→Spanner ETL lands in normal tables and the same data serves both SQL and GQL — no second copy, no separate graph ETL to maintain. That property is most of why this is deliverable at a customer.

**Schema:**

```
Nodes:   Agent, Principal, Session, Invocation, LlmCall, ToolCall,
         Tool, DataAsset, ExternalEndpoint

Edges:   (Principal)   -[:STARTED]->      (Session)
         (Session)     -[:HAS]->          (Invocation)
         (Invocation)  -[:PLANNED]->      (LlmCall)
         (LlmCall)     -[:DECIDED]->      (ToolCall)
         (ToolCall)    -[:OF_TOOL]->      (Tool)
         (ToolCall)    -[:READ]->         (DataAsset)
         (ToolCall)    -[:WROTE_TO]->     (ExternalEndpoint)
         (DataAsset)   -[:FLOWED_INTO]->  (LlmCall)     ← derived taint edge
         (Agent)       -[:RUNS_AS]->      (Principal)
```

`Tool` nodes carry the manifest properties from §0, so detections are written against tool *semantics*. Add a seventh tool next quarter and the queries still work.

`FLOWED_INTO` is load-bearing and **derived, not observed**: built by joining tool results to the LLM calls whose context window contained them — which is exactly what `before_model` captured in Lab 2.1. Attendees build this edge themselves. It's the difference between a pretty picture and a security graph.

**Spanner physical design** (also a portable lesson for customers):

- **Interleave the trajectory tables** — `Invocation` in `Session`, `LlmCall`/`ToolCall` in `Invocation`. Every trajectory query is session-scoped, so interleaving puts a whole trajectory in one split.
- **100 processing units.** ~200k edges is nothing for Spanner. Terraform pins 100 PU (~$0.09/hr regional); tell attendees explicitly not to provision a full node, because Argolis budgets are real.
- **Index the taint query's entry points** — `Tool.is_egress`, `DataAsset.trust_label`. Tiny tables, but that's where path search starts.

**The graph is an investigation tier, not a detection tier.** This distinction is load-bearing and the lab is built around it.

An unbounded `MATCH` over every untrusted asset with a `{1,4}` variable-length traversal is fine across 300 sessions and untenable at customer scale — 100k sessions/day is ~4M new edges/day, and unbounded path search over that is not something you run on a schedule. So the architecture is a **funnel**: Layer 1 rules flag candidate sessions cheaply, and the graph is queried *scoped to what was flagged*, by a human deciding whether it's real.

**The payoff query** — untrusted source reaching an egress sink, scoped to flagged sessions. Structurally the same query Wiz runs for cloud attack paths, and the same thing an analyst does by hand, minus the joins:

```sql
GRAPH TrajectoryGraph
MATCH (s:Session)-[:HAS]->(:Invocation)-[:PLANNED]->(l:LlmCall)
      -[:DECIDED]->(t:ToolCall)-[:OF_TOOL]->(tool:Tool)
WHERE s.session_id IN UNNEST(@flagged_sessions)
  AND tool.is_egress = TRUE
MATCH (d:DataAsset)-[:FLOWED_INTO]->(l)
WHERE d.trust_label = 'UNTRUSTED'
RETURN s.session_id, d.asset_id, t.tool_call_id, t.args_redacted
```

*(GQL syntax illustrative — validate against Spanner during build.)*

The query reveals **A2 (indirect injection)** without it being enumerated as a rule — it matches a *shape*, so it generalises to attacks nobody anticipated. It does **not** catch A3, and should not: A3 reads a TRUSTED record and mails it out, so there is no taint path at all. A3 belongs to Q4's question — *did a sensitive asset actually leave?* That's the argument for graph and it's the strongest idea in the workshop. But state the scaling caveat in the same breath, or attendees will carry a query to customers that works beautifully on demo data and times out in production.

**The lab task is triage, not query-running.** Attendees get ~8 sessions flagged by Layer 1 — some real attacks, some benign near-misses — and use the graph to decide which are which. That is the actual job, it exercises the near-miss stratification in T2, and it teaches the refinement (trust labels, approval checks) that a bare `RUN QUERY` never would.

### 4.4 Lab 2.4 — Layer 1: rules (45 min)

Deterministic BigQuery SQL over the trajectory tables, emitting to a common `findings` table. This is also the layer that becomes *enforcement* in §4.6, so precision here has consequences beyond alert volume.

Attendees wire the **Looker Studio template** to their own dataset first (~5 min, template ships pre-built — see ARCHITECTURE.md §8), then use it as the surface for the rest of 2.4 and 2.5. Its *Detection quality* page — precision/recall per rule against the labelled corpus — is where the precision/recall exercise below is actually read.

**Shipped working** (attendees read and run):
- **D1 Policy violation** — refund over limit; recipient off allowlist; tool invoked without preceding authorisation.
- **D3 Loop / exhaustion** — same `(tool, args_hash)` ≥ *k* within an invocation. Catches A5.
- **D5 Off-manifest tool** — tool called that this agent version doesn't declare.

**Attendees write** (these carry the teaching moment):
- **D2 Sensitive-read → egress** — PII read followed by egress within *N* steps with no human approval between. Catches A3, and forces them to think in sequences rather than predicates.
- **D4 Aggregation evasion** — windowed sum of refunds per session over limit. Catches A4, **which D1 misses entirely** — they have D1 in front of them, working, and it doesn't fire. Rules encode the attacks you already thought of.

Attendees measure precision and recall against the labelled T1 corpus. Every rule has false positives and they'll see exactly which benign trajectories trip it. That number is the honest cost of Layer 1 — and in §4.6 it becomes the cost of *blocking legitimate work*, which is a different conversation entirely.

### 4.5 Lab 2.5 — Layer 2 anomaly, Layer 3 judgment (50 min)

**Layer 2 — Anomaly (30 min).** Two techniques, deliberately:

- **Transition surprisal.** Build a tool-to-tool transition matrix from benign trajectories; score each new trajectory by summed negative log-probability. Pure SQL, no model training, catches novel sequences no rule enumerates. Best effort-to-insight ratio in the day.
- **Shape anomaly (BQML `KMEANS`).** Cluster trajectories on a feature vector — length, distinct tools, PII reads, egress count, error rate, wall-clock — and flag outliers with `ML.DETECT_ANOMALIES`.

**These two were measured to be complementary, not redundant.** On the shipped corpus surprisal catches A4 and A5, `KMEANS` catches A3, and the overlap is *zero*. Surprisal models **order** ("has this agent ever gone A→B?"); `KMEANS` models **shape** ("does this session's profile look like the population?"). A session using only ordinary transitions but making nine PII reads where the population makes one is invisible to the first and obvious to the second. `KMEANS` is also the day's only `CREATE MODEL`, so it carries the BQML coverage.

**Goal drift was built, measured and rejected — and that is a lab, not an omission.** Cosine distance between the user's opening request and the agent's actions fails for a reason no amount of tuning fixes: in A1, A3, A4 and A5 the user's own request *is* the attack, so the agent never deviates from it. A3 — the cleanest exfiltration in the suite — scored the *lowest* drift of all six. Spend five minutes on `sql/layer2/goal_drift_REJECTED.sql`: a plausible, widely-cited detector, disproven with two queries. It is the best argument in the day for measuring before deploying.

**What was cut, and what came back.** An earlier draft also had `ARIMA_PLUS` on call volume and `VECTOR_SEARCH` against a labelled attack corpus. Both are gone. `KMEANS` was cut too — and then **restored**, because the measurement contradicted the reasoning:

- `KMEANS` was cut as "overlapping transition surprisal". It does not overlap at all. Measured, they catch **disjoint** attacks — `KMEANS` catches A3, surprisal catches A4 and A5, and the intersection is empty. Surprisal models *order*; `KMEANS` models *shape*. It is now a labbed technique and the day's only `CREATE MODEL`.
- `ARIMA_PLUS` on volume duplicates what Scenario 1's managed dashboard already showed at 10:00. Teaching it again buys a technique, not an insight.
- `VECTOR_SEARCH` against known attacks is signature matching in embedding space — conceptually Layer 1 wearing a different hat, and it **requires a labelled attack corpus that no customer has**.

The two survivors need only *benign baseline traffic*, which every customer already has. That is not a coincidence, and it's worth saying out loud: **the simplification improved portability.** Attendees can stand these up at a customer on day one; the three cuts all carried a prerequisite the customer would have had to manufacture first.

**Explicit callout: BQML `KMEANS` as the Layer 2 alternative (5 min, no lab).** Show the `CREATE MODEL` statement and the feature vector it would use — trajectory length, distinct tools, PII reads, egress count, error rate, wall-clock — and say plainly when you would reach for it over surprisal:

- **Surprisal** models *order*. It answers "has this agent ever gone A→B before?" and fires on novel sequences.
- **`KMEANS`** models *shape*. It answers "is this session's overall profile unlike the population?" and fires on trajectories that use ordinary transitions in unusual proportions.

They fail differently, so at a customer with the budget to run both, both is a defensible answer. We teach surprisal because it needs no training step and explains itself when it fires — but attendees should leave knowing `CREATE MODEL ... KMEANS` exists, costs one query, and is the natural second Layer 2 signal. This is also the day's only exposure to training a model in BigQuery, so it doubles as BQML coverage.

**Layer 3 — Judgment (20 min).**

- `AI.GENERATE_BOOL` over a rendered trajectory: *"Did the agent's actions stay within what the user asked for?"* Measure agreement with ground-truth labels across the corpus.
- This catches **A6, the multi-turn crescendo**, which both layers below miss because each individual turn is genuinely benign.
- Run live over a **100-row sample** only, joining to pre-computed full-corpus results for the analysis — see §9.1 for why (Vertex quota at 40 attendees).

**Fusion and the honest tradeoff.** Combine layers into a weighted score; compare per-layer cost against per-layer catch rate. Layer 3 has the best recall on the hardest attacks and costs an LLM call per session — untenable at 100k sessions/day unless Layers 1–2 triage first. **Be explicit that the weights are not derived here.** Calibrating them against a customer's own false-positive tolerance is the part attendees will find hardest in the field, and pretending otherwise sets them up badly.

---

### 4.6 Lab 2.6 — Enforcement: closing the loop (25 min)

Everything up to here is post-hoc. A customer's first question is *"you detected the exfiltration four hours later — the email had already gone."* This capstone answers it.

**The hook is already instrumented.** `before_tool` has been capturing the decision record since Lab 2.1; it can also *refuse*. Attendees return a blocking response instead of `None` and the tool never executes — the agent receives a refusal it has to handle in-conversation.

Three enforcement modes, and choosing between them is the lesson:

| Mode | Behaviour | When |
|---|---|---|
| **Shadow** | Log what *would* have been blocked | Always first — measures FP cost before it hurts anyone |
| **Approve** | Divert to `escalate_to_human` | Irreversible-but-legitimate actions |
| **Block** | Refuse outright, return an error to the model | Unambiguous policy violation |

**The exercise:** promote **D2** (PII read → egress to a non-allowlisted domain) from detection to enforcement. Re-run A3 and watch the exfiltration fail. It is the most satisfying five minutes of the day, and it is the thing attendees will demo to their customers.

**Then the counterweight, which is the actual teaching point.** Turn on shadow mode over the benign corpus and count how many *legitimate* sessions D2 would have blocked. That number is the real cost of enforcement, and it explains why only Layer 1 is a candidate for it: you cannot inline-block on an LLM judge — the latency is prohibitive, the cost is per-call, and the false-positive rate is unbounded. **The least clever layer is the only one that can stop anything.** That inverts the ladder and it is the sentence to end the day on.

Enforcement decisions are themselves telemetry: a `blocked` outcome flows into BigQuery and the graph like any other, so the graph shows attacks that were stopped, not just attacks that happened.

---

## 5. Timing (9:00–17:00, 1h lunch, 2 breaks)

| Time | Duration | Block |
|---|---|---|
| 09:00 | 0:20 | Opening — agentic threat model, OWASP ASI, portability framing (§0) |
| 09:20 | 0:15 | Bootstrap: `terraform apply` + smoke test; **Spanner T2 load kicks off in background** |
| 09:35 | 1:10 | **Scenario 1** — Agent Engine; gap analysis against the FULL native surface (Application Monitoring, Observability Analytics, Cloud Trace) |
| 10:45 | 0:15 | Break |
| 11:00 | 1:00 | **Lab 2.1** — GKE deploy; native plane (Path A), the security 20% (Path B), joining the three telemetry planes; Pub/Sub pipeline via IaC |
| 12:00 | 0:45 | **Lab 2.2** — Red team + manual investigation |
| 12:45 | 0:45 | Lunch |
| 13:30 | 1:05 | **Lab 2.3** — Trajectory graph as an investigation tier |
| 14:35 | 0:15 | Break |
| 14:50 | 0:45 | **Lab 2.4** — Layer 1 rules (D1/D3/D5 shipped, D2/D4 written) |
| 15:35 | 0:30 | **Lab 2.5a** — Layer 2 anomaly |
| 16:05 | 0:20 | **Lab 2.5b** — Layer 3 judgment + fusion |
| 16:25 | 0:25 | **Lab 2.6** — Enforcement capstone |
| 16:50 | 0:10 | Wrap — hardening, baseline versioning, what we skipped |

Scenario 1 ≈ 1h25m with intro. Scenario 2 ≈ 4h50m. Content 6h45m.

Collapsing five detection layers to three (§1) freed the 25 minutes that Lab 2.6 now occupies. The day gains an enforcement capstone without getting longer.

**Aggressive, and only works with heavy pre-provisioning.** The Spanner background load during Scenario 1 is what makes the 13:30 graph lab start on time.

---

## 6. Build plan

| # | Deliverable | Notes |
|---|---|---|
| B1 | ShopFlow agent (ADK, 6 tools) | Runs unchanged on Agent Engine *and* GKE |
| B2 | Seed data | Customers, orders, KB docs — incl. the poisoned doc for A2 |
| B3 | Attack suite A1–A6 | Scripted, deterministic, re-runnable |
| B4 | Benign traffic generator | Persona/intent-driven; diversity is the requirement |
| B5 | **Tiered corpus (§2.3)** | T1 2k+200 in BQ; T2 300 stratified in Spanner; **validation gate first** |
| B6 | **Terraform, modularised by concern** | `agent-runtime/`, `telemetry-pipeline/`, `graph-store/`, `detections/`; targets a BYO Argolis project |
| B7 | **ADK telemetry adapter package** | `pip install` + one-line registration. Never lab snippets — a copy-paste pattern creates FDE work at every customer forever |
| B7a | **Native plane registration (Path A)** | The ADK BigQuery Agent Analytics plugin, registered ahead of ours in `harness/run.py`. This is the 80%, and it is Google's code — our package only has to carry what nothing native provides (`context_tool_call_ids`) |
| B7b | **Enforcement middleware** | `before_tool` blocking, ADK. Shadow / approve / block modes |
| B7c | Porting guide (docs only, no code) | Signal-by-signal mapping to other frameworks, so a second adapter is a day's work when a customer needs one (ARCHITECTURE.md §4.1) |
| B8 | Graph ETL | BQ → Spanner, incl. deriving `FLOWED_INTO` |
| B9 | Detection SQL | D1/D3/D5 complete; D2/D4 as guided exercises + reference solutions |
| B9b | **Non-security views** | `v_session_cost`, `v_agent_quality`, `v_tool_health` — cost, quality and reliability off the native plane. The tier that makes this trajectory monitoring rather than a security product with telemetry in it |
| B10 | Per-lab catch-up scripts | **Non-negotiable.** Every lab starts from seeded state |
| B10b | **Corpus load + site deploy scripts** | `labs/load_corpus.sh` (offline jsonl → BigQuery, creating tables from the schema contract so Terraform does not later plan a delete/create) and `labs/deploy_site.sh` (rebuild + deploy, pinning the `--ingress` that is the only thing keeping the guide behind IAP) |
| B11 | Tool manifest schema | The config file a customer edits first (§0) |
| B11b | **Version columns in the schema** | `agent_version` + `baseline_version` on every trajectory row and baseline model. Not exercised in any lab — but the schema must not preclude it (§6.1) |
| B12 | **Looker Studio template** (4 pages) | Shipped pre-built; attendees copy + repoint in ~5 min. See ARCHITECTURE.md §8 |
| B13 | Cloud Monitoring dashboard (Terraform) | Rebuilds the Scenario 1 dashboard on GKE — closes the arc |
| B14 | Lab guides + slides | |

Realistic authoring estimate: **65–90 engineering hours**, dominated by B5 and B8.

### 6.1 Baseline versioning — designed for, not built

Agents change constantly: prompt edits, model upgrades, new tools. A transition matrix fitted on agent v1 will alarm continuously after a v2 deploy, and that is what makes real deployments get switched off in week three.

**In scope:** `agent_version` and `baseline_version` columns throughout, so a trajectory is always scored against the baseline for its own version. **Out of scope for the workshop:** retraining triggers, drift alarms, baseline promotion. Covered in the wrap as a known production requirement — attendees need to know it exists before a customer discovers it for them.

**Build order is not arbitrary.** B5's validation gate comes before any lab content: if the corpus doesn't separate signal from noise, Lab 2.5 has nothing to show and that's a rewrite, not a fix. Sequence: B1–B4 → B5 + gate → B6–B9 → B10–B14.

**Argolis-specific checks before build:** Spanner and Vertex AI quota in attendee projects; org-policy constraints on GKE Autopilot and public GCS reads (the T2 corpus is served from a public bucket); whether the shared Spanner backup/import path is permitted cross-project.

---

## 7. Decisions — resolved

| # | Decision | Resolution |
|---|---|---|
| 1 | Project topology | **BYO Argolis project** per attendee; prebuilt Terraform + images for fast start |
| 2 | Graph store | **Spanner Graph.** No BigQuery fallback |
| 3 | Audience | Mixed. Focus on the *solution* and its portability (§0); ADK callbacks taught explicitly (§4.1) |
| 4 | Pub/Sub streaming | **Kept**, deployed via Terraform module for customer portability |
| 5 | Detections | Ship D1/D3/D5 working; attendees write **D2 and D4** |

### Still open

- **Argolis quota/org-policy verification** (see §6) — cheapest possible thing to get wrong, most expensive to discover on the day. `labs/preflight.sh` exists and has never been run end-to-end on a clean attendee project. **This is the last blocking item.**
- **`attack_real` is stale** — the real-model attack corpus was generated on `gemini-3.6-flash` and predates the 3.7 migration.

### Resolved since

- **Corpus generation model + budget** — settled. Real corpus is 1,730 sessions on `gemini-3.7-flash` (`corpus_real_37`, dataset `trajectory_37`), generated by `traffic/generate.py --model gemini-3.7-flash`. Vertex **quota**, not wall clock, is the binding constraint: concurrency 6 is stable with retry/backoff, concurrency 8 returns 429s.
- **Model choice** — `gemini-3.7-flash` throughout, including the in-BigQuery judge. `gemini-2.5-flash` retires 2026-10-16 and is gone from the repo. There is no model-comparison corpus any more; see the assurance framing in README.
- **The `AI.GENERATE_BOOL` regional constraint** — was never a constraint, only an endpoint *form*. Pass the fully-qualified global resource path.

---

## 8. Cost estimate

List prices, us-central1, **per attendee project** unless stated. 40 participants, each on their own Argolis with a ~$2,000/month budget — so per-person cost is the only figure that binds, and it is not close to binding. Validate against the pricing calculator before quoting these to anyone — Autopilot per-vCPU rates in particular are regional and were not verified.

### 8.1 Workshop day (~8h of uptime)

| Item | Basis | Cost |
|---|---|---|
| Spanner, 100 PU regional (**Enterprise**) | 8h × $0.123/hr | $0.98 |
| Spanner storage | T2 corpus, <1 GB | ~$0.00 |
| GKE cluster management fee | 8h × $0.10/hr | $0.80 |
| GKE Autopilot pods | 8h × ~1 vCPU + 4 GB | ~$0.51 |
| **Agent Engine (Scenario 1)** | 4h × 1 vCPU + 4 GB | **$0.00 — inside free tier** |
| Gemini inference | ~250 agent calls + sampled Layer 3 judge | $1–3 |
| BigQuery storage + query | <1 GB stored, ~20 GB scanned | ~$0.15 (mostly free tier) |
| Cloud NAT gateway (private-IP-only nodes) | 8h × $0.044/hr | $0.35 |
| Pub/Sub, GCS, Artifact Registry, Logging | | ~$0.10 |
| **Day total** | | **≈ $3–5** |

Cheaper than it looks, for two reasons worth knowing: Agent Engine's free tier (50 vCPU-hr + 100 GB-hr per month) fully absorbs a morning of Scenario 1 in a per-attendee project, and BigQuery's 1 TiB/month query allowance fully absorbs the detection labs. **Across 40 participants that is roughly $160 for the day** — though since each brings their own Argolis project, the only number that matters is the per-person one.

### 8.2 Left running for one week (168h), nothing torn down

| Item | Basis | Cost |
|---|---|---|
| Spanner, 100 PU (Enterprise) | 168h × $0.123 | $20.66 |
| GKE cluster management | 168h × $0.10 | $16.80 |
| GKE Autopilot pods, idle | 168h × ~$0.064 | $10.75 |
| Agent Engine, still deployed | 118 vCPU-hr + 572 GB-hr past free tier | $15.35 |
| Cloud NAT gateway | 168h × $0.044 | $7.39 |
| BigQuery storage + misc | | ~$1 |
| **Week total** | | **≈ $72** |

**≈ $72 per attendee (~$2,900 across 40, spread over 40 separate billing accounts).** Note the shape: the workshop itself was $5 and the remaining **~$67 is pure idle burn**. Everything expensive here is provisioned capacity sitting doing nothing.

### 8.3 Optimised week — keep the data, kill the runtime

Post-workshop, attendees want to keep writing GQL against the graph and SQL against the corpus. They do **not** need the agent running. So:

| Action | Saving |
|---|---|
| Delete Agent Engine deployment after Scenario 1 | −$15.35 |
| Delete the GKE cluster after Lab 2.2 — takes the router and NAT with it, same module (or scale to 0 replicas: −$10.75, NAT stays) | −$34.94 |
| **Keep** Spanner + BigQuery — the artifacts of the whole day | $20.68 |
| **Optimised week total** | **≈ $21** |

**≈ $21 per attendee (~$840 across 40) — a ~68% cut that loses nothing anyone actually wants.** This should be the documented default, with full teardown as the alternative and "leave everything up" as an explicit opt-in.

Spanner is then the only meaningful remaining line. Exporting the graph and deleting the instance drops it to ~$1/week, but that discards the thing the week was for.

### 8.4 One-time build cost (ours, not attendees')

| Item | Basis | Cost |
|---|---|---|
| Corpus generation (B5) | ~13k calls ≈ 39M in + 6.5M out, fast/cheap model | ~$28 |
| Dev iteration on the corpus | 3–5× the above | $85–140 |
| Dev project infra during build | Spanner + GKE, if torn down nightly | ~$60 |
| **Build total** | | **≈ $150–350** |

The corpus is not the expensive part. Tear the dev project's Spanner and GKE down nightly and this stays well under $400 — leaving them up for three weeks alone would roughly double it.

### 8.5 Cost risks and guardrails

| Risk | Impact | Guardrail |
|---|---|---|
| **Spanner provisioned as STANDARD edition** | **Hard failure — `CREATE PROPERTY GRAPH` is rejected and the graph lab cannot run at all** | Terraform pins `edition = "ENTERPRISE"`. Verified live: Spanner Graph is an Enterprise-only feature |
| Spanner provisioned as 1 node (1,000 PU) not 100 PU | **10× — ~$207/attendee/week** | Terraform pins `processing_units = 100`; call it out in the lab guide |
| GKE's $74.40/mo credit is per **billing account**, not per project | If Argolis projects share billing, only one cluster gets it | Assume no credit (as costed above); verify before the day |
| Someone “simplifies” by dropping Cloud NAT and giving nodes external IPs | **Hard failure on Argolis** — `constraints/compute.vmExternalIpAccess` rejects cluster creation; and it is the wrong shape to hand a customer | Design decision, not a workaround: **every VM is private-IP-only, egress via Cloud NAT.** Terraform pins `enable_private_nodes = true` and `depends_on` the NAT so ordering cannot regress. Costed in §8.1/8.2 |
| Traffic generator left running | Unbounded inference spend | Hard max-iterations in the generator |
| Layer 3 judge run over the full corpus in a loop | Grows fast from a small base | `LIMIT` in the lab query; ship full results pre-computed |
| Nobody tears down | The $72 case in §8.2 | Budget alert at $25/project + `bash labs/teardown.sh` (runtime only; `--all` for everything) |

The single highest-leverage guardrail is the Terraform pin on Spanner processing units. It is the only line item where a plausible mistake costs 10×.

### 8.6 Budget verdict at 40 participants

Against a ~$2,000/month per-person Argolis budget:

| Scenario | Per person | % of their monthly budget |
|---|---|---|
| Workshop day | ~$5 | **0.25%** |
| One week, fully optimised | ~$21 | **1.1%** |
| One week, nothing torn down | ~$72 | **3.6%** |

Cost is not a design constraint for this workshop and should stop being treated as one. Two consequences:

- **Don't inflate anything just because there's headroom.** The 100 PU Spanner pin and the 300-session T2 corpus were sized for *fitness*, not thrift — 100 PU is ample for 200k edges, and a bigger graph is a *worse* lab (§2.3). Leave both.
- **Teardown guidance stays, for a different reason.** The argument is no longer the week — it's the month nobody's watching. Left up and forgotten, this runs ~$255/month, or **13% of an attendee's budget indefinitely**. Frame teardown as hygiene, not economy.

**What the headroom does buy** (both worth considering as stretch content):
- **Layer 3 judge model comparison.** Run the judge with a cheap model and a frontier model over the same sample, compare agreement with ground truth. "Which model should I use as a judge, and does it actually matter?" is a question their customers will ask, and it costs a few dollars to answer empirically.
- **A live-detection stretch lab** for fast finishers, using the Pub/Sub path already deployed in §4.1.

---

## 8.7 Hosted lab guide

The guide is served at **https://trajectory.cedemo.app** — Cloud Run behind an
HTTPS LB in `waap-demo-323809`, IAP restricted to `domain:google.com`, then a
token gate (`trajectory`). Two live dashboards are published alongside it at
`/demo` (scripted corpus) and `/demo-real` (live-model corpus) so attendees can
see the finished product before Lab 0. Rebuild with `python site/build.py` and
redeploy; the guide markdown is the only source.

---

## 9. Delivering to 40 — what actually binds

At 40 participants the constraint moves off cost entirely. Three things bind instead.

### 9.1 Vertex AI quota is the real ceiling

40 people hitting Gemini in the same region inside the same 45-minute lab window is the genuine scaling risk, and Argolis projects often ship with modest default generative-model quota.

The spike is **Lab 2.5's Layer 3 judge**. If each attendee runs the judge over 300 sampled trajectories in a 20-minute window, that is ~12,000 calls across the room, ~600/min. Mitigation, mirroring the corpus-tiering logic in §2.3:

> **Sample, and record what was sampled.** The lab runs the judge live over a stratified sample (~300–400 sessions: everything Layers 1–2 flagged, the A6-shaped risk surface, plus 100 random benign) — enough to watch it work and measure agreement with ground truth. `sql/layer3/judge_coverage.sql` materialises that population into `judge_coverage` before the judge runs, and `v_detection_quality` scores Layer 3 against it, reporting `sessions_evaluated` and `basis` alongside precision and recall.
>
> This replaces an earlier plan to ship full-corpus judge results pre-computed. Sampling already caps the quota (~100–400 calls per attendee, not 2,000), and a pre-computed column would have hidden the more interesting problem: scored against all 1,992 sessions instead of the 386 it saw, the judge's recall read 0.957 where the honest number is 0.989. **Comparing detectors that were shown different populations is the bug; pre-computing would have papered over it.**

Secondary mitigation: make region a Terraform variable and split the room across 2–3 regions. Cheap insurance, no content impact.

Live agent traffic (T3, ~180 calls per attendee across a 45-min lab) is not a concern.

### 9.2 Pre-flight validation, run days ahead — not on the day

40 independent Argolis projects means 40 independent chances that someone's org policy blocks something. Discovering that at 09:20 with 39 people waiting is the single worst failure mode available to us.

Ship a **`preflight.sh`** that attendees run at least three days before, reporting back:

- Required APIs enabled (Spanner, GKE, Vertex AI, BigQuery, Pub/Sub, Artifact Registry)
- Gemini model access + current regional quota
- Org policy: public GCS read (the T2 corpus bucket), external IP / Autopilot constraints
- **Looker Studio access + ability to copy an externally-shared report** (org policy commonly blocks this) — *no longer lab-blocking*: the dashboard is generated locally by `labs/dashboard.py`, and the Looker copy is an optional handover exercise

  This has an owner-side half that no attendee check can catch. The dashboard is
  built **once by us** and copied by everyone — attendees never build one — so
  before the day: share the template so all 40 can reach it (Argolis org-only
  sharing will not span their tenancies), and confirm *"Disable downloading,
  printing and copying for viewers"* is **off**, or every copy link fails. Both
  failure modes are invisible to the owner, who always has access. Verify by
  having someone outside the org open the link cold.
- Spanner and GKE quota in the target region
- A trivial end-to-end smoke test: create and delete a 100 PU Spanner instance

Collect results centrally. Anyone red gets fixed before the day or gets a pre-provisioned backup project.

### 9.3 Facilitator ratio and the catch-up scripts

Hands-on labs at this depth need roughly **one floating helper per 10–12 attendees — so 3–4 people besides the presenter.** With 40 attendees, someone will be broken at every single lab boundary; that is a statistical certainty, not a risk.

This makes the per-lab catch-up scripts (B10) the highest-value item in the build plan, not merely a safety net. Each must fast-forward an attendee from *any* prior state to a correct starting state for the next lab in under two minutes, and the presenter should run one from the front at each transition so using it carries no stigma.

**Staggered `terraform apply` at 09:20.** GKE Autopilot cluster creation takes ~5–8 minutes and 40 concurrent image pulls will hit Artifact Registry at once. Pre-stage images in a multi-region repo, and start the Spanner T2 background load (§2.3) *before* the GKE apply so the slow path begins first.
