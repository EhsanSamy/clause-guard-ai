# Risk Rubric — Contract Risk Review Agent

Owner: Person 3 (drafted), sign-off from full team required before `prompts/score_prompt.md`
is written against it. This rubric is what `nodes/score_risk.py` must produce
`risk_score` values consistent with, and what `nodes/decide_and_route.py` uses to
compute `overall_risk_score` and routing decisions.

---

## 1. Per-Clause Risk Levels

Each clause gets exactly one of four levels. The scoring prompt must justify the
level chosen in `risk_rationale`, citing `retrieved_rule` wherever
`retrieved_rule_found` is `True`.

### Low
The clause matches, or is more favorable than, the playbook's "fair" position for
that category. No deviation that increases exposure, cost, or obligation beyond
what the playbook considers standard.

*Example:* A limitation-of-liability clause capped at 12 months' fees, matching the
playbook's stated acceptable range.

### Medium
The clause deviates from the playbook's fair position in a way that is common in
negotiation and not immediately dangerous, but should be flagged to a human
reviewer before signing. Deviation is bounded and typically negotiable.

*Example:* An auto-renewal clause with a 60-day opt-out notice window, where the
playbook recommends 30–45 days — worse than ideal, not unusual.

### High
The clause creates meaningful legal or financial exposure beyond what the playbook
tolerates, is a directional outlier, or removes a protection the playbook treats as
standard. Requires legal sign-off, not just a business owner's approval.

*Example:* Uncapped indemnification obligations where the playbook specifies a cap
tied to contract value.

### Critical
The clause creates severe, potentially unbounded exposure, contradicts a
non-negotiable playbook position, or combines with another flagged clause to
compound risk (e.g., uncapped liability *and* one-sided indemnification in the same
contract). Always routes to mandatory human review before any further automated
action; the system must never auto-approve a Critical clause.

*Example:* Governing law set to a jurisdiction the playbook explicitly excludes,
combined with a unilateral termination-for-convenience clause favoring only the
counterparty.

---

## 2. No-Playbook-Coverage Case

If `retrieved_rule_found` is `False` for a clause (i.e., retrieval found no matching
playbook rule for that clause's tag, or the clause was tagged `other`):

- The clause **cannot** be scored Low or Medium by default assumption of fairness.
- `risk_rationale` must explicitly state "no playbook coverage for this clause" —
  it must never fabricate a rule to cite (see `guardrails.md` — Hallucination).
- Default to `Medium` and `flagged_for_review = True`, unless the clause text
  contains an unambiguous severe-exposure pattern (e.g., unlimited liability,
  unilateral rights with no counterparty protection), in which case score `High`
  and flag for review. Never silently score `Low`.

This is the required edge case covered by `tests/test_no_playbook_match.py`.

---

## 3. Overall Case Risk (`overall_risk_score`)

Computed in `decide_and_route` from the set of per-clause scores:

| Condition | `overall_risk_score` |
|---|---|
| Any clause is `Critical` | `Critical` |
| No `Critical`, but ≥1 clause is `High` | `High` |
| No `High`/`Critical`, but ≥2 clauses are `Medium` | `Medium` |
| No `High`/`Critical`, 0–1 clause `Medium` | `Low` |

This is a simple worst-case-dominates aggregation, chosen deliberately: averaging
scores could mask a single dangerous clause inside an otherwise clean contract.

---

## 4. Routing Consequences

| `overall_risk_score` | Route |
|---|---|
| `Low` | Auto-generate report, no human gate required |
| `Medium` | Report generated, recommended (not mandatory) reviewer check |
| `High` | Report generated, mandatory reviewer sign-off before any downstream action |
| `Critical` | Report generated, hard block on any automated approval; escalation flag set |

---

## 5. Reflection Interaction

The `reflect` node may re-examine scores before finalizing (see the Reflection
pattern). A revision is only valid if:
- It changes `risk_score` for at least one clause, **and**
- The revised `risk_rationale` still cites `retrieved_rule` (or states no
  coverage) — reflection cannot remove the citation requirement.

`tests/test_reflection_revision.py` is the proof that this loop can actually change
an outcome, not just re-state the same score in different words.
