# Problems & Solutions

While building the AI Finance Controller, I found that the difficult part was not connecting the three CSV files. The difficult part was making the reconciliation logic reliable when the data was messy, preserving the right evidence between stages, and adding Gemini without allowing it to become the source of financial truth.

## 1. Inconsistent financial data broke the first layer

### What failed

The three CSV sources did not use one consistent representation for amounts or dates. Amounts arrived as strings, formatted rupee values, and sometimes blank optional fields. Dates also appeared in multiple formats.

My first date parser handled only two formats. During late-night testing, valid records were being rejected simply because their dates were written differently. I also found that canonical values could be converted from rupees to paise twice, which changed a value such as `97640` into `9764000`. Raw strings such as `"1000.00"` also failed once the canonical models correctly expected integer paise.

### How I solved it

I moved normalization to the ingestion boundary. Raw monetary values are converted to integer paise exactly once, canonical models only validate paise, and dates are normalized to ISO values using explicit formats plus a `dateutil` fallback. I also separated required monetary fields from optional Razorpay debit/credit fields so valid blank optional fields do not become false validation failures.

---

## 2. Dead-letter handling had to distinguish bad data from valid-but-unresolved data

### What failed

I initially had to deal with rows that were malformed, missing required values, or contained invalid dates or amounts. At the same time, some rows were perfectly valid but simply could not be reconciled.

Treating both cases the same would either hide bad input or incorrectly discard valid financial records. The system also needed to continue processing the rest of the batch when one row was malformed.

### How I solved it

I made validation row-level and preserved failures as dead letters with their source information instead of silently dropping them. Valid but unresolved records continue through the reconciliation stages. This gives me a clear distinction between:

```text
invalid input → dead letter
valid but unresolved → reconciliation exception
```

That distinction is important for auditability because an invalid CSV row and a genuine financial mismatch are different problems.

---

## 3. Fuzzy matching was producing cases that looked right but were not financially safe

### What failed

A high RapidFuzz similarity score could make a candidate look convincing even when the amount or date did not agree. In the adversarial data, I found cases where identifiers were highly similar but the financial difference was large enough that accepting the candidate would create a false reconciliation.

I also found that the test data itself could make some intended fuzzy cases look “wrong”: a candidate might be selected because the identifier was similar, but then correctly remain an exception because the amount or date gate failed.

### How I solved it

I made Stage 2 a guarded fuzzy layer instead of a similarity-only matcher. A candidate must satisfy the configured similarity, amount, and date conditions before it can become a fuzzy match.

The important rule became:

```text
similar identifier ≠ financial match
```

I also preserved every failed gate so I could see whether the problem was the score, amount, date, or multiple conditions together. I did not weaken the matching rules just to make the synthetic data produce more matches.

---

## 4. The adversarial dataset exposed deeper deterministic bugs

### What failed

Once I moved from clean sample data to deliberately adversarial CSVs, the pipeline exposed problems that normal tests did not reveal.

These included:

- missing identifiers becoming `NaN`/null join keys;
- signed and parenthesized negative amounts being interpreted incorrectly;
- `None` and `NaN` being treated like real identifiers;
- strict date-window checks being affected by truncated day calculations;
- refund or adjustment rows being considered as payment candidates;
- duplicate bank UTRs producing multiple settlement outputs.

One of these became a full backend failure: an exact amount-mismatch path reached Pydantic with `gateway_order_id = NaN` and caused an HTTP 500 instead of returning a normal reconciliation result.

### How I solved it

I treated these as deterministic correctness problems, not frontend problems. I hardened normalization and identifier handling, protected exact joins from invalid keys, handled signed/refund values correctly, tightened date comparisons, separated payment rows from non-payment activity, and addressed duplicate-reference cases. I then added regression coverage around the failure paths.

---

## 5. Stage 2 had a control-flow bug and an evidence-loss bug

### What failed

I found two separate Stage 2 problems.

First, an unresolved record could be appended during the fuzzy-matching loop and then appended again during a cleanup pass. The result was duplicate Stage 2 handoffs for the same financial case.

Second, a valid bank UTR was being lost from the canonical schema. A truncated UTR that was actually highly similar to the correct bank UTR therefore appeared to have a poor score when the system compared it against the longer bank narration instead.

The diagnostic comparison was:

```text
Full UTR vs truncated bank UTR: 0.9714
Full UTR vs bank narration:     0.5397
```

### How I solved it

I changed the control flow so every record takes one path:

```text
Auto-match → match result
Not auto-matched → exactly one Stage 3 handoff
```

I also preserved the canonical bank UTR and made it the primary fuzzy comparison field, with narration used only as a fallback. This taught me that the matching algorithm is only as reliable as the evidence that reaches it.

---

## 6. The Gemini layer required a completely controlled contract

### What failed

The biggest architectural problem was deciding how Gemini should behave in a financial system.

A language model can produce a convincing explanation even when the underlying evidence does not support a match. If I allowed Gemini to invent a candidate, report a new amount, or return a generic resolved status, the deterministic controls would no longer mean much.

I also needed the model to explain unresolved cases without exposing operational identifiers unnecessarily.

### How I solved it

I kept Gemini strictly in Stage 3, after deterministic processing.

For each Stage 3 request, I build trusted grounding from the merchant, Razorpay, and bank records and create a temporary request-scoped mapping between real identifiers and masked tokens. For example:

```text
INV-2026-1010   → MERCHANT_ORDER_001
ORDER_Q1010XZ   → GATEWAY_ORDER_001
SETL_S005       → SETTLEMENT_001
HDFC...0444     → SETTLEMENT_UTR_001
```

Gemini receives the masked/tokenized evidence, not the real operational IDs. The model response schema was also tightened so the model must return structured fields such as:

```text
status
error_code
reasoning
reported_amount_paise
source_record_token
candidate_record_token
human_approval_required
```

The reasoning field is schema-constrained, and the returned amount must already exist in trusted evidence. Returned source and candidate tokens are checked against the request's token metadata and their allowed roles.

The backend then checks three separate things:

```text
1. Is the response structurally valid?
2. Does the reported financial value match trusted evidence?
3. Do the returned tokens exist and have the correct source/candidate role?
```

I also added status gating. A `STRONG_POTENTIAL_MATCH` can only come from a Stage 2 `POTENTIAL_FUZZY_MATCH`, cannot follow a failed amount/date gate, and must explicitly require human approval. A Stage 2 exception cannot be promoted into a strong potential match.

If Gemini fails validation, times out, or cannot satisfy the grounding checks, I retry through the configured model chain and then fall back to:

```text
NEEDS_MANUAL_REVIEW
```

This makes Gemini an evidence-grounded reasoning layer rather than a reconciliation engine.

---

## 7. The final integration exposed a reviewer-UI data problem

### What failed

The first versions of the frontend could show that a transaction matched or that an exception existed, but the reviewer needed more than a status. For exception cases, the UI needed the actual source record, selected candidate, IDs, amounts, dates, similarity score, amount/date variance, failed gates, and lookup information.

Without those fields in the backend response, the frontend would have had to reconstruct information or rely on Gemini's prose, which would have weakened the trust boundary.

### How I solved it

I expanded the response schema so the backend returns trusted reviewer evidence separately from the LLM explanation. This includes fields such as:

```text
source_record_id
candidate_record_id
review_evidence
comparison
similarity_score
amount_diff_paise
date_diff_days
failed_gates
review_lookup
review_state
```

The frontend now renders these backend-owned fields directly. It does not parse IDs from Gemini's reasoning and does not recalculate financial results.

I also separated the reviewer experience into strong-potential-match and exception/manual-review views, while keeping reviewer actions session-only and separate from the deterministic reconciliation result.

---

## What these failures changed in the final design

The problems changed the architecture in a useful way.

I ended up with a strict progression:

```text
Validate and normalize
        ↓
Stage 1 — deterministic exact reconciliation
        ↓
Stage 2 — guarded fuzzy reconciliation
        ↓
Stage 3 — grounded Gemini reasoning
        ↓
Schema + numeric + identifier validation
        ↓
Human review where required
```

The final principle I built around is:

> **I use code to prove financial relationships, preserve evidence when they cannot be proved, and use Gemini only to help explain the unresolved remainder.**
