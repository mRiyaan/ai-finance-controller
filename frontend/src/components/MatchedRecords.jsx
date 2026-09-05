import { formatPaise } from "@/lib/formatters";

function MatchPill({ children, tone = "success" }) {
  return <span className={`match-pill match-pill-${tone}`}>{children}</span>;
}

function EmptyMatchState({ message }) {
  return <p className="empty-match-state">{message}</p>;
}

function LedgerMatchTable({ records, title }) {
  if (records.length === 0) {
    return (
      <EmptyMatchState message={`No ${title.toLowerCase()} were returned for this batch.`} />
    );
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Merchant order</th>
            <th>Gateway order</th>
            <th>Razorpay entity</th>
            <th>Settlement ID</th>
            <th>Amount</th>
            <th>Method</th>
          </tr>
        </thead>

        <tbody>
          {records.map((record, index) => (
            <tr
              key={`${record?.gateway_order_id ?? "ledger-match"}-${index}`}
            >
              <td>{record?.merchant_order_id ?? "—"}</td>
              <td className="monospace-cell">
                {record?.gateway_order_id ?? "—"}
              </td>
              <td className="monospace-cell">
                {record?.razorpay_entity_id ?? "—"}
              </td>
              <td className="monospace-cell">
                {record?.razorpay_settlement_id ?? "—"}
              </td>
              <td>{formatPaise(record?.amount_paise)}</td>
              <td>
                <MatchPill
                  tone={
                    String(record?.match_method ?? "").includes("FUZZY")
                      ? "primary"
                      : "success"
                  }
                >
                  {record?.match_method ?? "MATCHED"}
                </MatchPill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SettlementMatchTable({ records, title }) {
  if (records.length === 0) {
    return (
      <EmptyMatchState message={`No ${title.toLowerCase()} were returned for this batch.`} />
    );
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Settlement ID</th>
            <th>Settlement UTR</th>
            <th>Bank reference</th>
            <th>Expected net</th>
            <th>Bank credit</th>
            <th>Method</th>
          </tr>
        </thead>

        <tbody>
          {records.map((record, index) => (
            <tr key={`${record?.settlement_id ?? "settlement-match"}-${index}`}>
              <td className="monospace-cell">
                {record?.settlement_id ?? "—"}
              </td>
              <td className="monospace-cell">
                {record?.settlement_utr ?? "—"}
              </td>
              <td className="monospace-cell">
                {record?.bank_reference ?? "—"}
              </td>
              <td>{formatPaise(record?.expected_net_paise)}</td>
              <td>{formatPaise(record?.bank_credit_paise)}</td>
              <td>
                <MatchPill
                  tone={
                    String(record?.match_method ?? "").includes("FUZZY")
                      ? "primary"
                      : "success"
                  }
                >
                  {record?.match_method ?? "MATCHED"}
                </MatchPill>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MatchGroup({ title, description, count, children }) {
  return (
    <details className="match-group">
      <summary>
        <span>
          <strong>{title}</strong>
          <small>{description}</small>
        </span>

        <MatchPill>{count} record{count === 1 ? "" : "s"}</MatchPill>
      </summary>

      <div className="match-group-content">{children}</div>
    </details>
  );
}

export default function MatchedRecords({ reconciliationData }) {
  const exactLedgerMatches =
    reconciliationData?.matched_ledger_razorpay ?? [];
  const fuzzyLedgerMatches = reconciliationData?.fuzzy_ledger_matches ?? [];
  const exactSettlementMatches =
    reconciliationData?.matched_settlements_bank ?? [];
  const fuzzySettlementMatches =
    reconciliationData?.fuzzy_settlement_matches ?? [];

  const backendApprovedMatchCount = reconciliationData?.summary
    ?.backend_approved_match_count;

  const totalMatches = Number.isSafeInteger(backendApprovedMatchCount)
    ? backendApprovedMatchCount
    : null;

  return (
    <section className="matched-records-section" aria-labelledby="matched-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Backend-approved outcomes</p>
          <h2 id="matched-title">Deterministically matched records</h2>
        </div>

        <span className="data-state">
          {totalMatches === null
            ? "Match count unavailable"
            : `${totalMatches} matched record${totalMatches === 1 ? "" : "s"}`}
        </span>
      </div>

      <div className="matched-records-notice">
        <div className="notice-icon" aria-hidden="true">
          ✓
        </div>

        <div>
          <h3>Resolved by backend rules, not Gemini</h3>
          <p>
            These records were resolved by Stage 1 exact checks or Stage 2
            constrained fuzzy checks. The frontend displays the
            backend-approved outcome and never changes it.
          </p>
        </div>
      </div>

      <div className="match-group-list">
        <MatchGroup
          count={exactLedgerMatches.length}
          description="Merchant ledger ↔ Razorpay records matched by exact identifier and amount checks."
          title="Exact ledger–Razorpay matches"
        >
          <LedgerMatchTable
            records={exactLedgerMatches}
            title="Exact ledger–Razorpay matches"
          />
        </MatchGroup>

        <MatchGroup
          count={fuzzyLedgerMatches.length}
          description="Unresolved ledger records that passed the Stage 2 score, amount, and date guardrails."
          title="Constrained fuzzy ledger matches"
        >
          <LedgerMatchTable
            records={fuzzyLedgerMatches}
            title="Constrained fuzzy ledger matches"
          />
        </MatchGroup>

        <MatchGroup
          count={exactSettlementMatches.length}
          description="Razorpay settlement ↔ bank records matched by exact UTR and expected-net checks."
          title="Exact settlement–bank matches"
        >
          <SettlementMatchTable
            records={exactSettlementMatches}
            title="Exact settlement–bank matches"
          />
        </MatchGroup>

        <MatchGroup
          count={fuzzySettlementMatches.length}
          description="Unresolved settlement-bank records that passed all Stage 2 fuzzy matching gates."
          title="Constrained fuzzy settlement matches"
        >
          <SettlementMatchTable
            records={fuzzySettlementMatches}
            title="Constrained fuzzy settlement matches"
          />
        </MatchGroup>
      </div>
    </section>
  );
}