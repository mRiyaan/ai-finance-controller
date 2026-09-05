"use client";

import { useState } from "react";
import DeterministicResults from "@/components/DeterministicResults";
import HeroVisual from "@/components/HeroVisual";
import MatchedRecords from "@/components/MatchedRecords";
import Stage3ReviewerQueue from "@/components/Stage3ReviewerQueue";
import SummaryMetrics from "@/components/SummaryMetrics";
import UploadBatchForm from "@/components/UploadBatchForm";

const workflowSteps = [
  {
    number: "01",
    title: "Exact matching",
    description:
      "Stage 1 uses deterministic order-ID and settlement-UTR checks, then verifies amounts in integer paise.",
  },
  {
    number: "02",
    title: "Constrained fuzzy matching",
    description:
      "Only unresolved records move forward. Score, amount-tolerance, and date guardrails must pass.",
  },
  {
    number: "03",
    title: "Grounded review assistance",
    description:
      "Stage 3 can explain trusted evidence, but it never changes deterministic reconciliation truth.",
  },
];

function ResultsEmptyState() {
  return (
    <section
      className="results-empty-state"
      aria-labelledby="results-empty-title"
    >
      <p className="eyebrow">Results workspace</p>
      <h2 id="results-empty-title">Awaiting a reconciliation batch</h2>
      <p>
        Upload the merchant ledger, Razorpay settlement report, and bank
        statement above. Once FastAPI returns a response, this dashboard will
        display backend-approved summary metrics and deterministic exceptions.
      </p>
    </section>
  );
}

export default function DashboardShell() {
  const [reconciliationData, setReconciliationData] = useState(null);

  const hasSuccessfulReconciliation = Boolean(reconciliationData);

  return (
    <main className="dashboard">
      <section className="hero-section">
        <div className="hero-content">
          <div className="hero-kicker-row">
            <span className="hero-kicker-dot" aria-hidden="true" />
            <p className="eyebrow">Recon AI Financial control center</p>
          </div>

          <h2>Verify every rupee across your payment flow.</h2>

          <p className="hero-copy">
            Reconcile merchant orders, Razorpay settlement activity, and bank
            credits with deterministic matching first—then route only genuine
            exceptions to grounded human review.
          </p>

          <div className="hero-trust-row">
            <span className="hero-trust-item">
              <span aria-hidden="true">✓</span>
              Integer-paise precision
            </span>

            <span className="hero-trust-item">
              <span aria-hidden="true">✓</span>
              No force-matching
            </span>

            <span className="hero-trust-item">
              <span aria-hidden="true">✓</span>
              Human review retained
            </span>
          </div>

          <div className="hero-operational-row">
            <div className="hero-backend-status">
              <span
                className={`hero-backend-dot ${
                  hasSuccessfulReconciliation ? "is-processed" : ""
                }`}
                aria-hidden="true"
              />
              <div>
                <strong>
                  {hasSuccessfulReconciliation
                    ? "Batch processed locally"
                    : "Ready for reconciliation"}
                </strong>
                <span>FastAPI backend connected locally</span>
              </div>
            </div>

            <div className="hero-stage-count">
              <strong>3</strong>
              <span>guarded reconciliation stages</span>
            </div>
          </div>
        </div>

        <HeroVisual />
      </section>

      

      <UploadBatchForm onReconciliationSuccess={setReconciliationData} />

      {hasSuccessfulReconciliation ? (
        <>
          <SummaryMetrics reconciliationData={reconciliationData} />
          <MatchedRecords reconciliationData={reconciliationData} />
          <DeterministicResults reconciliationData={reconciliationData} />
          <Stage3ReviewerQueue reconciliationData={reconciliationData} />
        </>
      ) : (
        <ResultsEmptyState />
      )}

      <section className="workflow-section" aria-labelledby="workflow-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">System design</p>
            <h2 id="workflow-title">Three-stage reconciliation</h2>
          </div>
        </div>

        <div className="workflow-grid">
          {workflowSteps.map((step) => (
            <article className="workflow-step" key={step.number}>
              <span>{step.number}</span>
              <h3>{step.title}</h3>
              <p>{step.description}</p>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}