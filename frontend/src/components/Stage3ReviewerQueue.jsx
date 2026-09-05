"use client";

import { useEffect, useState } from "react";
import { formatPaise } from "@/lib/formatters";

function formatScore(score) {
  return typeof score === "number" && Number.isFinite(score)
    ? `${(score * 100).toFixed(1)}%`
    : "—";
}

function formatDateOffset(days) {
  if (!Number.isInteger(days)) {
    return "Not available";
  }

  if (days === 0) {
    return "0 days";
  }

  return `${days > 0 ? "+" : ""}${days} days`;
}

function Value({ children, mono = false }) {
  return (
    <span className={mono ? "evidence-value monospace-cell" : "evidence-value"}>
      {children ?? "—"}
    </span>
  );
}

function StatusTag({ children, tone = "neutral" }) {
  return <span className={`review-status review-status-${tone}`}>{children}</span>;
}

function GateList({ gates }) {
  if (!Array.isArray(gates) || gates.length === 0) {
    return <span className="gate-empty">No failed gates returned</span>;
  }

  return (
    <div className="gate-list">
      {gates.map((gate) => (
        <StatusTag key={gate} tone="error">
          {gate}
        </StatusTag>
      ))}
    </div>
  );
}

function EvidenceFieldList({ title, record }) {
  const entries = Object.entries(record ?? {}).filter(
    ([, value]) =>
      value !== null &&
      value !== undefined &&
      value !== "" &&
      typeof value !== "object"
  );

  return (
    <div className="evidence-column">
      <h5>{title}</h5>

      {entries.length === 0 ? (
        <p className="evidence-empty">No trusted fields returned.</p>
      ) : (
        <dl className="evidence-list">
          {entries.map(([key, value]) => {
            const isPaise = key.endsWith("_paise");

            return (
              <div className="evidence-row" key={key}>
                <dt>{key.replaceAll("_", " ")}</dt>
                <dd>
                  {isPaise && Number.isSafeInteger(value)
                    ? `${formatPaise(value)} (${value} paise)`
                    : String(value)}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}

function ReviewEvidence({ result }) {
  const evidence = result?.review_evidence ?? {};
  const comparison = evidence?.comparison ?? {};
  const lookup = evidence?.review_lookup ?? {};

  return (
    <details className="evidence-details">
      <summary>View trusted evidence</summary>

      <div className="evidence-content">
        <div className="evidence-meta-grid">
          <div>
            <span>Exception ID</span>
            <Value mono>{evidence?.exception_id}</Value>
          </div>

          <div>
            <span>Comparison type</span>
            <Value>{evidence?.comparison_type}</Value>
          </div>

          <div>
            <span>Comparison field</span>
            <Value>{evidence?.comparison_field}</Value>
          </div>

          <div>
            <span>Similarity score</span>
            <Value>{formatScore(comparison?.similarity_score)}</Value>
          </div>

          <div>
            <span>Amount difference</span>
            <Value>
              {Number.isSafeInteger(comparison?.amount_diff_paise)
                ? `${formatPaise(comparison.amount_diff_paise)} (${comparison.amount_diff_paise} paise)`
                : "—"}
            </Value>
          </div>

          <div>
            <span>Date difference</span>
            <Value>{formatDateOffset(comparison?.date_diff_days)}</Value>
          </div>
        </div>

        <div className="evidence-record-grid">
          <EvidenceFieldList
            record={evidence?.source_record}
            title="Trusted source record"
          />

          <EvidenceFieldList
            record={evidence?.candidate_record}
            title="Trusted candidate record"
          />
        </div>

        <div className="evidence-bottom-grid">
          <div>
            <h5>Failed deterministic gates</h5>
            <GateList gates={comparison?.failed_gates} />
          </div>

          <div>
            <h5>Uploaded CSV lookup keys</h5>
            <dl className="evidence-list evidence-list-compact">
              <div className="evidence-row">
                <dt>Merchant</dt>
                <dd className="monospace-cell">
                  {lookup?.merchant_csv_search ?? "—"}
                </dd>
              </div>

              <div className="evidence-row">
                <dt>Razorpay</dt>
                <dd className="monospace-cell">
                  {lookup?.razorpay_csv_search ?? "—"}
                </dd>
              </div>

              <div className="evidence-row">
                <dt>Bank</dt>
                <dd className="monospace-cell">
                  {lookup?.bank_csv_search ?? "—"}
                </dd>
              </div>
            </dl>
          </div>
        </div>
      </div>
    </details>
  );
}

function getReviewKey(result, index) {
  return `${result?.record_id ?? "review-item"}-${index}`;
}

function getStateTone(reviewState) {
  if (reviewState === "MATCHED_BY_REVIEWER") {
    return "success";
  }

  if (reviewState === "CONFIRMED_EXCEPTION") {
    return "error";
  }

  if (reviewState === "INVESTIGATED") {
    return "primary";
  }

  return "warning";
}

function formatReviewState(reviewState) {
  return String(reviewState ?? "PENDING_REVIEW")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function ReviewerActions({
  decisionState,
  onDecision,
  queueType,
  reviewState,
}) {
  if (queueType === "strong") {
    return (
      <div className="review-actions">
        <button
          className="review-action-button review-action-confirm"
          disabled={reviewState === "MATCHED_BY_REVIEWER"}
          onClick={() => onDecision("MATCHED_BY_REVIEWER")}
          type="button"
        >
          Confirm match
        </button>

        <button
          className="review-action-button review-action-reject"
          disabled={reviewState === "CONFIRMED_EXCEPTION"}
          onClick={() => onDecision("CONFIRMED_EXCEPTION")}
          type="button"
        >
          Reject candidate
        </button>

        {decisionState && (
          <p className="review-action-message" aria-live="polite">
            Session-only review state updated. It will reset after refresh.
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="review-actions">
      <button
        className="review-action-button review-action-exception"
        disabled={reviewState === "CONFIRMED_EXCEPTION"}
        onClick={() => onDecision("CONFIRMED_EXCEPTION")}
        type="button"
      >
        Confirm exception
      </button>

      <button
        className="review-action-button review-action-investigate"
        disabled={reviewState === "INVESTIGATED"}
        onClick={() => onDecision("INVESTIGATED")}
        type="button"
      >
        Mark investigated
      </button>

      {decisionState && (
        <p className="review-action-message" aria-live="polite">
          Session-only review state updated. It will reset after refresh.
        </p>
      )}
    </div>
  );
}

function ReviewerCard({
  decisionState,
  onDecision,
  result,
  queueType,
}) {
  const isStrongPotential = queueType === "strong";
  const statusTone = isStrongPotential ? "success" : "error";
  const llmStatus = result?.llm_status ?? "NEEDS_MANUAL_REVIEW";
  const failedGates = result?.stage2_failed_gates ?? [];
  const usedFallback = result?.used_fallback === true;
  const reviewState = decisionState ?? result?.review_state ?? "PENDING_REVIEW";

  return (
    <article className="review-card">
      <div className="review-card-header">
        <div>
          <div className="review-card-tags">
            <StatusTag tone={statusTone}>{llmStatus}</StatusTag>
            <StatusTag tone="neutral">
              {result?.source ?? "unknown source"}
            </StatusTag>
            <StatusTag tone={getStateTone(reviewState)}>
              {formatReviewState(reviewState)}
            </StatusTag>
          </div>

          <h4>{result?.record_id ?? "Unknown reconciliation item"}</h4>
        </div>

        <StatusTag tone={usedFallback ? "warning" : "primary"}>
          {usedFallback ? "Fallback used" : "Validated response"}
        </StatusTag>
      </div>

      <div className="review-id-grid">
        <div>
          <span>Trusted source ID</span>
          <Value mono>{result?.source_record_id}</Value>
        </div>

        <div>
          <span>Trusted candidate ID</span>
          <Value mono>{result?.candidate_record_id}</Value>
        </div>
      </div>

      <div className="review-metric-grid">
        <div>
          <span>Stage 2 outcome</span>
          <Value>{result?.stage2_status}</Value>
        </div>

        <div>
          <span>Amount variance</span>
          <Value>
            {Number.isSafeInteger(result?.ground_truth_amount_diff_paise)
              ? `${formatPaise(result.ground_truth_amount_diff_paise)} (${result.ground_truth_amount_diff_paise} paise)`
              : "—"}
          </Value>
        </div>

        <div>
          <span>Human approval</span>
          <Value>
            {result?.human_approval_required ? "Required" : "Not required"}
          </Value>
        </div>

        <div>
          <span>Numeric validation</span>
          <Value>
            {result?.numeric_cross_check_passed ? "Passed" : "Not passed"}
          </Value>
        </div>

        <div>
          <span>Identifier validation</span>
          <Value>
            {result?.identifier_cross_check_passed ? "Passed" : "Not passed"}
          </Value>
        </div>

        <div>
          <span>Model used</span>
          <Value mono>{result?.llm_model_used ?? "Fallback/no model"}</Value>
        </div>
      </div>

      <div className="review-reasoning">
        <span>Gemini assistance</span>
        <p>{result?.llm_reasoning ?? "No reasoning returned."}</p>
      </div>

      {usedFallback && (
        <div className="fallback-notice">
          <strong>Fallback reason:</strong>{" "}
          {result?.fallback_reason ?? "No fallback reason returned."}
        </div>
      )}

      <div className="failed-gates-section">
        <span>Stage 2 failed gates</span>
        <GateList gates={failedGates} />
      </div>

      <ReviewerActions
        decisionState={decisionState}
        onDecision={onDecision}
        queueType={queueType}
        reviewState={reviewState}
      />

      <ReviewEvidence result={result} />
    </article>
  );
}

function QueueSection({
  decisions,
  onDecision,
  title,
  description,
  items,
  queueType,
}) {
  const isStrongPotential = queueType === "strong";

  return (
    <section className="review-queue-section">
      <div className="review-queue-heading">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>

        <StatusTag tone={isStrongPotential ? "success" : "error"}>
          {items.length} item{items.length === 1 ? "" : "s"}
        </StatusTag>
      </div>

      {items.length === 0 ? (
        <div className="review-empty-state">
          <p>
            {isStrongPotential
              ? "No strong potential matches were returned for this batch."
              : "No exception or manual-review items were returned for this batch."}
          </p>
        </div>
      ) : (
        <div className="review-card-list">
          {items.map(({ result, index }) => {
            const reviewKey = getReviewKey(result, index);

            return (
              <ReviewerCard
                decisionState={decisions[reviewKey]}
                key={reviewKey}
                onDecision={(newState) => onDecision(reviewKey, newState)}
                queueType={queueType}
                result={result}
              />
            );
          })}
        </div>
      )}
    </section>
  );
}

export default function Stage3ReviewerQueue({ reconciliationData }) {
  const [decisions, setDecisions] = useState({});

  useEffect(() => {
    setDecisions({});
  }, [reconciliationData]);

  const stage3Results = reconciliationData?.stage3_results ?? [];

  const indexedResults = stage3Results.map((result, index) => ({
    result,
    index,
  }));

  const strongPotentialMatches = indexedResults.filter(
    ({ result }) => result?.llm_status === "STRONG_POTENTIAL_MATCH"
  );

  const exceptionReviewItems = indexedResults.filter(
    ({ result }) =>
      result?.llm_status === "EXCEPTION" ||
      result?.llm_status === "NEEDS_MANUAL_REVIEW"
  );

  function handleDecision(reviewKey, newState) {
    setDecisions((currentDecisions) => ({
      ...currentDecisions,
      [reviewKey]: newState,
    }));
  }

  return (
    <section className="reviewer-section" aria-labelledby="reviewer-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Stage 3 reviewer workspace</p>
          <h2 id="reviewer-title">Grounded review queues</h2>
        </div>

        <span className="data-state">
          {stage3Results.length} review item
          {stage3Results.length === 1 ? "" : "s"}
        </span>
      </div>

      <div className="reviewer-truth-notice">
        <div className="notice-icon" aria-hidden="true">
          !
        </div>

        <div>
          <h3>Reviewer decisions are session-only</h3>
          <p>
            Decisions below update local browser state only. They never rewrite
            deterministic reconciliation results, update the backend, call
            Gemini, or persist after refresh.
          </p>
        </div>
      </div>

      <QueueSection
        decisions={decisions}
        description="Only validated Stage 3 recommendations. Confirming a match is a human action and changes only the local reviewer state."
        items={strongPotentialMatches}
        onDecision={handleDecision}
        queueType="strong"
        title="Strong potential matches"
      />

      <QueueSection
        decisions={decisions}
        description="Deterministic exceptions and manual-review responses. Confirming an exception or marking investigation updates only this browser session."
        items={exceptionReviewItems}
        onDecision={handleDecision}
        queueType="exception"
        title="Exceptions and manual review"
      />
    </section>
  );
}