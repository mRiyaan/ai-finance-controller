import { formatPaise } from "@/lib/formatters";

function EmptyTableState({ message }) {
  return <p className="empty-table-state">{message}</p>;
}

function StatusPill({ children, tone = "neutral" }) {
  return <span className={`result-pill result-pill-${tone}`}>{children}</span>;
}

function AmountMismatchTable({ records }) {
  if (records.length === 0) {
    return (
      <EmptyTableState message="No ledger amount mismatches were returned for this batch." />
    );
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Merchant order</th>
            <th>Gateway order</th>
            <th>Merchant amount</th>
            <th>Razorpay amount</th>
            <th>Difference</th>
            <th>Status</th>
          </tr>
        </thead>

        <tbody>
          {records.map((record, index) => {
            const merchantAmount = record?.merchant_amount_paise;
            const razorpayAmount = record?.razorpay_amount_paise;

            const difference =
              Number.isSafeInteger(merchantAmount) &&
              Number.isSafeInteger(razorpayAmount)
                ? merchantAmount - razorpayAmount
                : null;

            return (
              <tr key={`${record?.gateway_order_id ?? "ledger"}-${index}`}>
                <td>{record?.merchant_order_id ?? "—"}</td>
                <td className="monospace-cell">
                  {record?.gateway_order_id ?? "—"}
                </td>
                <td>{formatPaise(merchantAmount)}</td>
                <td>{formatPaise(razorpayAmount)}</td>
                <td>{formatPaise(difference)}</td>
                <td>
                  <StatusPill tone="error">
                    {record?.match_method ?? "AMOUNT_MISMATCH"}
                  </StatusPill>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SettlementMismatchTable({ records }) {
  if (records.length === 0) {
    return (
      <EmptyTableState message="No settlement-to-bank amount mismatches were returned for this batch." />
    );
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Settlement ID</th>
            <th>Settlement UTR</th>
            <th>Expected net</th>
            <th>Bank credit</th>
            <th>Difference</th>
            <th>Status</th>
          </tr>
        </thead>

        <tbody>
          {records.map((record, index) => {
            const expectedNet = record?.expected_net_paise;
            const bankCredit = record?.bank_credit_paise;

            const difference =
              Number.isSafeInteger(expectedNet) &&
              Number.isSafeInteger(bankCredit)
                ? expectedNet - bankCredit
                : null;

            return (
              <tr key={`${record?.settlement_id ?? "settlement"}-${index}`}>
                <td className="monospace-cell">
                  {record?.settlement_id ?? "—"}
                </td>
                <td className="monospace-cell">
                  {record?.settlement_utr ?? record?.bank_reference ?? "—"}
                </td>
                <td>{formatPaise(expectedNet)}</td>
                <td>{formatPaise(bankCredit)}</td>
                <td>{formatPaise(difference)}</td>
                <td>
                  <StatusPill tone="error">
                    {record?.match_method ?? "SETTLEMENT_AMOUNT_MISMATCH"}
                  </StatusPill>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RawRowDetails({ record }) {
  const rawRow = record?.raw_row;

  if (!rawRow || typeof rawRow !== "object") {
    return <span className="raw-row-empty">Original CSV row unavailable.</span>;
  }

  const entries = Object.entries(rawRow);

  return (
    <details className="dead-letter-details">
      <summary>View original CSV row</summary>
      <div className="raw-row-panel">
        <div className="raw-row-heading">
          <span>Original source record</span>
          <span className="raw-row-meta">
            {record?.source ?? "source"} · backend row {record?.row_index ?? "—"}
          </span>
        </div>

        <dl className="raw-row-grid">
          {entries.map(([field, value]) => (
            <div className="raw-row-field" key={field}>
              <dt>{field}</dt>
              <dd>{value === null || value === undefined || value === "" ? "—" : String(value)}</dd>
            </div>
          ))}
        </dl>
      </div>
    </details>
  );
}

function DeadLettersTable({ records }) {
  if (records.length === 0) {
    return (
      <EmptyTableState message="No invalid source rows were retained as dead letters." />
    );
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Row</th>
            <th>Error code</th>
            <th>Error message</th>
            <th>Original record</th>
          </tr>
        </thead>

        <tbody>
          {records.map((record, index) => (
            <tr key={`${record?.source ?? "source"}-${record?.row_index ?? index}`}>
              <td>{record?.source ?? "—"}</td>
              <td>{record?.row_index ?? "—"}</td>
              <td>
                <StatusPill tone="warning">
                  {record?.error_code ?? "SCHEMA_VALIDATION_FAILED"}
                </StatusPill>
              </td>
              <td className="error-message-cell">
                {record?.error_message ?? "No error message returned."}
              </td>
              <td className="dead-letter-details-cell">
                <RawRowDetails record={record} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function DeterministicResults({ reconciliationData }) {
  const amountMismatches = reconciliationData?.amount_mismatches ?? [];
  const settlementAmountMismatches =
    reconciliationData?.settlement_amount_mismatches ?? [];
  const deadLetters = reconciliationData?.dead_letters ?? [];

  return (
    <section
      className="deterministic-results-section"
      aria-labelledby="deterministic-results-title"
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">Stage 1 and Stage 2 outputs</p>
          <h2 id="deterministic-results-title">
            Deterministic exceptions and validation records
          </h2>
        </div>

        <span className="data-state">Read-only backend results</span>
      </div>

      <div className="result-stack">
        <article className="result-panel">
          <div className="result-panel-heading">
            <div>
              <h3>Ledger amount mismatches</h3>
              <p>
                Exact identifier links where merchant and Razorpay amounts do
                not match.
              </p>
            </div>

            <StatusPill tone="error">{amountMismatches.length}</StatusPill>
          </div>

          <AmountMismatchTable records={amountMismatches} />
        </article>

        <article className="result-panel">
          <div className="result-panel-heading">
            <div>
              <h3>Settlement-bank amount mismatches</h3>
              <p>
                Exact settlement UTR links where expected net and bank credit
                differ.
              </p>
            </div>

            <StatusPill tone="error">
              {settlementAmountMismatches.length}
            </StatusPill>
          </div>

          <SettlementMismatchTable records={settlementAmountMismatches} />
        </article>

        <article className="result-panel">
          <div className="result-panel-heading">
            <div>
              <h3>Dead letters</h3>
              <p>
                Invalid input rows preserved by the backend instead of being
                dropped from the reconciliation batch.
              </p>
            </div>

            <StatusPill tone="warning">{deadLetters.length}</StatusPill>
          </div>

          <DeadLettersTable records={deadLetters} />
        </article>
      </div>
    </section>
  );
}