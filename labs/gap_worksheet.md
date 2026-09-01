# Lab 1 — Gap worksheet

Fill this in using **only** the native surface. Do not open BigQuery.

Google's managed observability is genuinely good and has moved a long way. Use
all of it, or this lab teaches you a gap that does not exist:

- **Agent Engine dashboard** — tokens, latency, error rate, tool calls over time
- **Cloud Trace** — the full span waterfall for every invocation
- **Application Monitoring** — agent-aware dashboards built on the
  OpenTelemetry **GenAI semantic conventions**, which ADK emits natively
- **Observability Analytics** — SQL over logs *and* traces, including aggregate
  queries: failure rate per tool, P95 latency per tool, across every run
- **Cloud Logging** — the raw records

Run each attack against your Agent Engine deployment, then answer honestly.

| | **A5** tool loop / cost DoS | **A3** PII exfiltration chain | **A2** indirect prompt injection |
|---|---|---|---|
| Did anything alert you? | | | |
| Could you find it if you knew to look? | | | |
| What signal did you use? | | | |
| How long did it take? | | | |
| Would you have found it at 3am, in a fleet of 200 agents? | | | |

Then one more, and take it seriously rather than treating it as rhetorical:

> **Which of these could you have written a query for *in advance*, without
> already knowing the attack existed?**

---

## What we expect you to find

Compare only after you have written your own answers.

**A5 — yes, and easily.** Token spend and call volume spike; Observability
Analytics will give you calls-per-tool-per-session in one query. Managed
observability is *good* at operational anomalies. This is a real capability, not
a consolation prize, and you should say so to a customer.

**A3 — findable, not detected.** Every span is in Cloud Trace. You can see
`lookup_customer`. You can see `send_email`. You can write a query that finds
sessions containing both — the native tier is more than capable of it. What
nothing does is *decide that the pair is a finding*, because "read PII then
emailed someone" describes a large fraction of legitimate support traffic. You
will find A3 if you already suspected it. The gap is precision, not visibility.

**A2 — no, and this one is structural.** The knowledge base returned
attacker-controlled text, and the model then called a tool. Every individual
event is recorded. What is *not* recorded anywhere in the native plane is
whether the untrusted content was in the context window when the model made
that decision. Causality inside the context window is not an operational
metric, so nothing collects it — and no query over the data that exists can
recover it after the fact.

---

## The point

> **Operational telemetry tells you what the agent did. It cannot tell you what
> influenced it.**

That is a narrower claim than "managed observability misses attacks", and it is
the one that survives contact with an informed customer. Scenario 1 gives you
tokens, latency, error rates, call volume, span waterfalls and SQL over all of
it. A2 is not missed because the tooling is weak. It is missed because the
question is a *security* question and general-purpose observability has no
reason to ask it.

So the afternoon is not about rebuilding any of the above — you keep all of it,
and Lab 2.1 registers the native plane first for exactly that reason. It is
about the **one field** nobody collects for you, and what becomes derivable once
you do.

Write down which of the three bothers you most. You will build the detection for
it this afternoon.
