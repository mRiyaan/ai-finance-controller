# Evaluation and Testing

## 1. What is being measured

The project separates three different ideas that are easy to mix together:

1. **Reconciliation resolution** — how many valid records were resolved by the pipeline.
2. **Stage 3 validation quality** — whether Gemini's structured response agrees with trusted deterministic evidence.
3. **Model accuracy** — a statistical property that requires independent labelled ground truth.

The current synthetic evaluation provides strong evidence for (1) and (2). It does not justify claiming a general-purpose model accuracy percentage.

---

## 2. Automated test suite

The finalized backend test suite was verified with:

```text
65 tests passed
```

The test coverage spans the deterministic stages and the Stage 3 safeguards.

Important cases include:

- exact order-ID matching
- exact settlement UTR matching
- amount mismatch behavior
- fuzzy identifier matching
- threshold boundaries
- date-gate behavior
- duplicate protection
- UTR extraction
- raw-to-paise conversion
- dead-letter handling
- Stage 3 masking/grounding
- numeric cross-checks
- identifier cross-checks
- invalid LLM output fallback
- retry and model-chain behavior
- reviewer-state behavior

Run:

```bash
cd backend
pytest -q
```

---

## 3. Manual Swagger verification

The backend was manually exercised through Swagger with the three CSV inputs.

The API contract is:

```text
POST /reconcile
multipart/form-data

merchant_file
razorpay_file
bank_file
```

The successful response contains deterministic result collections, Stage 3 results and a summary block.

---

## 4. Final adversarial batch

The production-style adversarial batch contained:

```text
84 merchant rows
89 Razorpay rows
64 bank rows
```

After validation:

```text
83 valid merchant rows
88 valid Razorpay rows
63 valid bank rows
```

The invalid rows were preserved as dead letters.

### Outcome summary

```text
70 exact ledger matches
4 fuzzy ledger matches
74 ledger reconciled links

5 settlement-bank matches
0 fuzzy settlement-bank matches

4 ledger amount mismatches
1 settlement amount mismatch

11 Stage 3 handoffs
3 dead letters

79 backend-approved matches
```

The backend response also exposes the aggregate values explicitly instead of asking the frontend to reconstruct them.

---

## 5. Resolution rate

For the merchant ledger:

```text
74 reconciled links / 83 valid merchant rows
= 89.2%
```

This can reasonably be called:

> **ledger reconciliation resolution rate**

It should not be called:

> model accuracy

because the denominator contains unresolved-but-valid records and the synthetic dataset is not an independently labelled statistical benchmark.

---

## 6. Why 79 is not "79 out of all rows"

There are different populations and relationships in the pipeline.

The verified response separately reports:

```text
ledger_reconciled_link_count = 74
settlement_bank_reconciled_link_count = 5
backend_approved_match_count = 79
```

The 79 number is an aggregate backend outcome across the reconciliation links.

The frontend is not allowed to infer this from visible table lengths.

---

## 7. Stage 3 validation evidence

The production result contains explicit fields such as:

```text
llm_model_used
models_attempted
ground_truth_amount_diff_paise
numeric_cross_check_passed
identifier_cross_check_passed
human_approval_required
used_fallback
```

One verified result, for example, used:

```text
gemini-3.1-flash-lite
```

and returned:

```text
numeric_cross_check_passed: true
identifier_cross_check_passed: true
human_approval_required: true
used_fallback: false
```

This is a stronger statement than simply saying "Gemini gave a good answer" because the model output is being checked against trusted evidence.

---

## 8. Example exception that stayed an exception

The adversarial batch contains a ledger case where:

```text
amount difference = 79,900 paise
date difference   = 8 days
similarity        ≈ 0.818
```

The failed gates include:

```text
AMOUNT_MISMATCH
DATE_OFFSET_EXCEEDED
BELOW_AUTO_MATCH_THRESHOLD
```

The model explains the discrepancy, but the backend retains:

```text
human_approval_required = true
```

The important point is that good-looking identifier similarity did not override the financial controls.

---

## 9. Example potential fuzzy case

Another settlement candidate had:

```text
amount difference = 0
date difference   = 2 days
similarity        ≈ 0.778
```

That places it in the potential-fuzzy band rather than the auto-match band.

The Stage 3 result described it as:

```text
STRONG_POTENTIAL_MATCH
```

while still requiring human confirmation.

That is exactly the intended human-in-the-loop boundary.

---

## 10. Dead-letter verification

The adversarial run retained three invalid rows:

- one merchant row with an invalid amount
- one Razorpay row with malformed required fields/date
- one bank row with an invalid amount

The response contains the raw source record for each dead letter.

The frontend makes the raw row expandable for reviewer inspection.

This is important because a batch should not look "clean" simply because the invalid rows disappeared.

---

## 11. What a proper accuracy experiment would require

To report a genuine accuracy number later, the project should use an independently labelled dataset.

For each eligible candidate pair, the evaluator would know:

```text
true match
true non-match
```

Then measure something like:

```text
precision
recall
false-positive rate
false-negative rate
```

For Stage 2 specifically, the most useful measurement would be precision/recall for fuzzy matching under the configured amount/date thresholds.

For Stage 3, evaluation should score whether the model:

- correctly classifies the deterministic exception
- avoids inventing amounts
- preserves the correct identifiers
- requests human review when required
- avoids unsafe strong-match recommendations

That would be a future evaluation track, not something to claim from the current synthetic resolution numbers.

---

## 12. Recommended demo wording

Use:

> “On our verified adversarial batch, the backend resolved 74 of 83 valid merchant records and 5 settlement-to-bank links, for 79 backend-approved reconciliation links. The remaining cases were intentionally surfaced as mismatches, review items and dead letters. We report that as reconciliation resolution, not as model accuracy.”

This wording is technically honest and still communicates that the system was tested beyond a happy path.

---

## 13. Final release gate

Before submission, the release should show all of the following:

```text
65 tests passed
        +
Cloud Run /reconcile → HTTP 200
        +
79 backend-approved links
        +
11 Stage 3 handoffs
        +
3 dead letters
        +
live Vercel frontend
```

The combination is much stronger evidence than a single successful example.

---

## 14. Current limitations

The evaluation is still bounded by:

- synthetic data
- limited batch sizes
- no independent labelled benchmark
- session-only reviewer decisions
- stateless execution
- provider-specific schema assumptions

These are acceptable MVP limitations as long as they are stated clearly and not hidden behind an inflated accuracy percentage.
