# Reconciliation Pipeline

## Purpose

This document explains what happens to one batch after the user uploads the merchant ledger, Razorpay settlement report and bank statement.

The pipeline deliberately becomes more expensive and more interpretive only as records become harder to reconcile.

---

## 1. Ingestion

The frontend sends one `multipart/form-data` request:

```text
POST /reconcile
```

with:

```text
merchant_file
razorpay_file
bank_file
```

FastAPI reads the CSVs and normalizes them into canonical records.

---

## 2. Cleaning and validation

The raw-to-canonical boundary performs:

- amount conversion to integer paise
- datetime normalization
- identifier normalization
- UTR extraction where necessary
- row-level validation

Invalid source rows are preserved as dead letters.

A malformed row therefore becomes something the reviewer can inspect, rather than something that disappears from the batch.

---

## 3. Stage 1 — exact ledger reconciliation

For every merchant record with a usable gateway order ID, the engine checks the Razorpay report.

The ideal case is:

```text
merchant.gateway_order_id
        =
razorpay.order_id

AND

merchant.gross_amount_paise
        =
razorpay.amount_paise
```

### Outcomes

```text
EXACT_ORDER_ID
AMOUNT_MISMATCH
NO_EXACT_ORDER_ID
```

Exact matches are finalized by deterministic code.

Amount mismatches stay visible as exceptions.

Only the unresolved portion enters Stage 2.

---

## 4. Stage 1 — settlement reconciliation

Razorpay rows are aggregated by settlement.

The expected net is computed from the actual report values:

```text
sum(amount)
- sum(fee)
- sum(tax)
```

The bank comparison then uses:

```text
settlement UTR
↔
bank UTR / reference
```

with exact net-credit equality.

### Outcomes

```text
EXACT_UTR
SETTLEMENT_AMOUNT_MISMATCH
NO_EXACT_UTR
```

A 1-paisa difference is still an exact-stage mismatch. The later 500-paise fuzzy tolerance is not applied to a record that already has an exact UTR.

---

## 5. Stage 2 — fuzzy ledger matching

Stage 2 starts only with ledger records that did not resolve in Stage 1.

The candidate search compares order identifiers using RapidFuzz.

Current controls:

```text
auto threshold      0.85
potential floor     0.75
amount tolerance    500 paise
date tolerance      3 days
```

### Auto-match

A fuzzy candidate may become an automatic match only when:

```text
score >= 0.85
amount difference <= 500 paise
date difference <= 3 days
```

where a missing date can be treated as unavailable evidence rather than an automatic failure.

### Potential fuzzy

A candidate in the band:

```text
0.75 <= score < 0.85
```

can be surfaced as a potential review candidate if amount/date gates also pass.

It is not called a match.

### Exception

Anything below the potential floor, or anything that fails the amount/date controls, remains an exception.

---

## 6. Stage 2 — fuzzy settlement matching

Settlement UTRs follow the same philosophy.

A truncated or mistyped UTR can produce a strong similarity score, but the score alone never proves a financial match.

The candidate must also satisfy:

- amount tolerance
- date tolerance
- unambiguous allocation

Selected candidate evidence includes the canonical bank row index so the reviewer can trace the candidate back to the exact source row.

---

## 7. Failed gates are evidence

The system does not replace multiple failures with one vague message.

For an unresolved candidate the response can preserve, for example:

```json
[
  "AMOUNT_MISMATCH",
  "DATE_OFFSET_EXCEEDED",
  "BELOW_AUTO_MATCH_THRESHOLD"
]
```

The primary error code follows finance-first ordering:

```text
AMOUNT_MISMATCH
DATE_OFFSET_EXCEEDED
LOW_FUZZY_SCORE
BELOW_AUTO_MATCH_THRESHOLD
```

That makes the exception explainable without asking the LLM to infer what the deterministic engine already knows.

---

## 8. Stage 3 — grounded exception reasoning

Stage 3 is entered only for unresolved records.

The handoff contains:

- source record
- selected candidate if one exists
- Stage 2 score
- amount difference
- date difference
- failed gates
- lookup information
- trusted review evidence

The model does not get to choose a different candidate.

### Structured result

The response is constrained to:

```text
STRONG_POTENTIAL_MATCH
EXCEPTION
NEEDS_MANUAL_REVIEW
```

The backend validates that structure before accepting it.

---

## 9. Numeric and identifier validation

Stage 3 includes backend cross-checking.

For example, an LLM cannot report a new financial amount that was not already present in trusted evidence.

A valid result should show:

```text
numeric_cross_check_passed: true
identifier_cross_check_passed: true
```

when the model's reported values agree with the deterministic evidence.

When validation fails or Gemini is unavailable, the safe outcome is:

```text
NEEDS_MANUAL_REVIEW
```

---

## 10. Human review

The frontend keeps reviewer actions separate from reconciliation truth.

Examples:

```text
STRONG_POTENTIAL_MATCH
    → Confirm match
    → Reject candidate

EXCEPTION / NEEDS_MANUAL_REVIEW
    → Confirm exception
    → Mark investigated
```

These actions are session-local in the current MVP.

A refresh resets them.

The point is not to create a fake accounting system; the point is to demonstrate a controlled path from automated reconciliation to grounded human review.

---

## 11. Final backend response

The backend returns separate collections for:

```text
matched_ledger_razorpay
fuzzy_ledger_matches
amount_mismatches
matched_settlements_bank
fuzzy_settlement_matches
settlement_amount_mismatches
stage3_handoffs
stage3_results
dead_letters
summary
```

The `summary` is backend-owned.

This avoids frontend arithmetic such as independently adding arrays and accidentally producing a different total.

---

## 12. Why the pipeline is intentionally staged

The progression is:

```text
Stage 1
Cheap + deterministic
      ↓
Stage 2
More flexible + still rule-constrained
      ↓
Stage 3
Reasoning for unresolved cases
      ↓
Human review
```

That is the core design trade-off: increase flexibility only after certainty has been exhausted.

---

## 13. What the pipeline proved on the adversarial batch

The verified adversarial run produced:

```text
70 exact ledger matches
4 fuzzy ledger matches
74 ledger reconciled links

5 settlement-bank exact matches
0 fuzzy settlement-bank matches

1 settlement amount mismatch
11 Stage 3 handoffs
3 dead letters
79 backend-approved matches
```

The result is intentionally mixed. A finance controller that reports "everything matched" would be less credible than one that exposes the records it could not prove.

---

## 14. Reviewer mental model

When explaining the pipeline in a panel:

> “The system first tries to prove the transaction with exact IDs and exact integer-paise arithmetic. Only the unresolved remainder enters fuzzy matching, where similarity is still gated by amount, date and uniqueness. Anything left over is handed to Gemini with the deterministic evidence already attached. Gemini can explain the case, but it cannot rewrite the underlying reconciliation result.”
