import { formatCount } from "@/lib/formatters";

const metricDefinitions = [
  {
    key: "matched_ledger_razorpay_count",
    fallback: "matchedLedgerRazorpay",
    label: "Exact ledger matches",
    description: "Merchant orders matched to Razorpay by exact order ID.",
    tone: "success",
  },
  {
    key: "fuzzy_ledger_match_count",
    fallback: "fuzzyLedgerMatches",
    label: "Fuzzy ledger matches",
    description: "Constrained Stage 2 matches that passed all backend gates.",
    tone: "primary",
  },
  {
    key: "amount_mismatch_count",
    fallback: "amountMismatches",
    label: "Ledger amount mismatches",
    description: "Exact IDs found, but authoritative amounts differ.",
    tone: "error",
  },
  {
    key: "matched_settlements_bank_count",
    fallback: "matchedSettlementsBank",
    label: "Settlement-bank matches",
    description: "Settlement UTR and backend-approved net credit match.",
    tone: "success",
  },
  {
    key: "settlement_amount_mismatch_count",
    fallback: "settlementAmountMismatches",
    label: "Settlement mismatches",
    description: "Settlement UTR found, but expected net and bank credit differ.",
    tone: "error",
  },
  {
    key: "stage3_handoff_count",
    fallback: "stage3Results",
    label: "Review items",
    description: "Unresolved records routed to grounded human review.",
    tone: "warning",
  },
  {
    key: "dead_letter_count",
    fallback: "deadLetters",
    label: "Dead letters",
    description: "Invalid source rows retained for investigation.",
    tone: "neutral",
  },
];

function getMetricValue(summary, definition, collections) {
  const summaryValue = summary?.[definition.key];

  if (Number.isSafeInteger(summaryValue)) {
    return summaryValue;
  }

  return collections[definition.fallback]?.length ?? 0;
}

export default function SummaryMetrics({ reconciliationData }) {
  const summary = reconciliationData?.summary ?? {};

  const collections = {
    matchedLedgerRazorpay:
      reconciliationData?.matched_ledger_razorpay ?? [],
    fuzzyLedgerMatches: reconciliationData?.fuzzy_ledger_matches ?? [],
    amountMismatches: reconciliationData?.amount_mismatches ?? [],
    matchedSettlementsBank:
      reconciliationData?.matched_settlements_bank ?? [],
    settlementAmountMismatches:
      reconciliationData?.settlement_amount_mismatches ?? [],
    stage3Results: reconciliationData?.stage3_results ?? [],
    deadLetters: reconciliationData?.dead_letters ?? [],
  };

  return (
    <section className="summary-section" aria-labelledby="summary-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Backend-approved summary</p>
          <h2 id="summary-title">Reconciliation overview</h2>
        </div>

        <span className="data-state">Batch response loaded</span>
      </div>

      <div className="metric-grid">
        {metricDefinitions.map((metric) => (
          <article
            className={`metric-card metric-card-${metric.tone}`}
            key={metric.key}
          >
            <span className="metric-label">{metric.label}</span>

            <strong className="metric-value">
              {formatCount(getMetricValue(summary, metric, collections))}
            </strong>

            <p>{metric.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}