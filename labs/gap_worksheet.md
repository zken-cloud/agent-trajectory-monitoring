# Lab 1 — Gap worksheet

Fill this in using **only** Cloud Trace, Cloud Logging and the Agent Engine
dashboard. Do not open BigQuery.

Run each attack against your Agent Engine deployment, then answer honestly.

| | **A5** tool loop / cost DoS | **A3** PII exfiltration chain | **A1** goal hijack |
|---|---|---|---|
| Did anything alert you? | | | |
| Could you find it if you knew to look? | | | |
| What signal did you use? | | | |
| How long did it take? | | | |
| Would you have found it at 3am, in a fleet of 200 agents? | | | |

---

## What we expect you to find

Compare only after you have written your own answers.

**A5 — yes.** The dashboard shows it plainly: call volume and token spend spike.
Managed observability is genuinely good at operational anomalies, and this is a
real capability, not a consolation prize.

**A3 — partially.** Every span is in Cloud Trace. You can see `lookup_customer`
and you can see `send_email`. What does not exist is anything that **joins** them
and calls the pair a finding. You will find it if you already suspected it.

**A1 — no.** Nothing in the managed tooling evaluates whether the agent's
trajectory matched what the user asked for. There is no signal to look for,
because intent is not a metric.

---

## The point

> **Operational observability is not security detection.**

Scenario 1 gave you tokens, latency, error rates and call volume for free, and
those are worth having. But the three questions a security team actually asks —
*did untrusted content influence a tool call*, *did sensitive data leave*, *did
the agent do what was asked* — are not answerable from any of it.

Everything in Scenario 2 exists to answer those three questions. Write down
which one bothers you most; you will build the detection for it this afternoon.
