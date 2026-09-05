from typing import Any, Dict, List

import pandas as pd
from pydantic import ValidationError

from .cleaners import get_bank_reference_or_utr, normalize_amount_to_paise
from .schemas import (
    AmountMismatchRecord,
    CanonicalBankRow,
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    DeadLetterRow,
    FullReconciliationResult,
    MatchedLedgerRazorpay,
    MatchedSettlementBank,
    ReconciliationResult,
    SettlementAmountMismatch,
    Stage3HandoffResponse,
    UnresolvedRecord,
)
from .stage2_fuzzy_match import reconcile_fuzzy


def _sanitize_pandas_na(value: Any) -> Any:
    """Convert pandas/numpy NA scalars to None recursively."""
    if isinstance(value, dict):
        return {key: _sanitize_pandas_na(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_pandas_na(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_pandas_na(item) for item in value)
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _optional_amount_to_paise(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str) and value.strip() == "":
        return 0
    return normalize_amount_to_paise(value)


def _load_merchant_df(
    raw_df: pd.DataFrame,
) -> tuple[List[CanonicalMerchantRow], List[DeadLetterRow]]:
    clean_rows: List[CanonicalMerchantRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            mapped = {
                "merchant_order_id": row.get("merchant_order_id"),
                "gateway_order_id": row.get("gateway_order_id"),
                "gross_amount_paise": normalize_amount_to_paise(
                    row.get("gross_amount")
                ),
                "order_created_at": row.get("order_created_at"),
                "customer_reference": row.get("customer_reference"),
                "order_status": row.get("order_status"),
                "source_system": row.get("source_system"),
            }
            clean_rows.append(CanonicalMerchantRow(**mapped))
        except (ValidationError, ValueError, KeyError, TypeError) as error:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="merchant",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(error),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _load_razorpay_df(
    raw_df: pd.DataFrame,
) -> tuple[List[CanonicalRazorpayRow], List[DeadLetterRow]]:
    clean_rows: List[CanonicalRazorpayRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            mapped = {
                "transaction_entity": row.get("transaction_entity"),
                "entity_id": row.get("entity_id"),
                "amount_paise": normalize_amount_to_paise(row.get("amount")),
                "currency": row.get("currency"),
                "fee_paise": _optional_amount_to_paise(
                    row.get("fee (exclusive tax)")
                ),
                "tax_paise": _optional_amount_to_paise(row.get("tax")),
                "debit_paise": _optional_amount_to_paise(row.get("debit")),
                "credit_paise": _optional_amount_to_paise(row.get("credit")),
                "payment_method": row.get("payment_method"),
                "entity_created_at": row.get("entity_created_at"),
                "payment_captured_at": row.get("payment_captured_at"),
                "order_id": row.get("order_id"),
                "settlement_id": row.get("settlement_id"),
                "settled_at": row.get("settled_at"),
                "settlement_utr": row.get("settlement_utr"),
                "settled_by": row.get("settled_by"),
            }
            clean_rows.append(CanonicalRazorpayRow(**mapped))
        except (ValidationError, ValueError, KeyError, TypeError) as error:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="razorpay",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(error),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _load_bank_df(
    raw_df: pd.DataFrame,
) -> tuple[List[CanonicalBankRow], List[DeadLetterRow]]:
    clean_rows: List[CanonicalBankRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            reference_number = row.get("reference_number")
            description = row.get("description")
            utr = get_bank_reference_or_utr(reference_number, description)

            mapped = {
                "transaction_date": row.get("transaction_date"),
                "value_date": row.get("value_date"),
                "description": description,
                "reference_number": reference_number,
                "debit_paise": _optional_amount_to_paise(row.get("debit")),
                "credit_paise": _optional_amount_to_paise(row.get("credit")),
                "balance_paise": _optional_amount_to_paise(row.get("balance")),
                "currency": row.get("currency"),
                "utr": utr,
            }
            clean_rows.append(CanonicalBankRow(**mapped))
        except (ValidationError, ValueError, KeyError, TypeError) as error:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="bank",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(error),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _aggregate_razorpay_by_settlement(
    razorpay_rows: List[CanonicalRazorpayRow],
) -> List[Dict[str, Any]]:
    dataframe = pd.DataFrame([row.model_dump() for row in razorpay_rows])
    if dataframe.empty:
        return []

    dataframe = dataframe[
        dataframe["settlement_id"].notna()
        & dataframe["settlement_id"].astype(str).str.strip().ne("")
    ].copy()
    if dataframe.empty:
        return []

    grouped = (
        dataframe.groupby("settlement_id", dropna=True)
        .agg(
            gross_sum_paise=("amount_paise", "sum"),
            fee_sum_paise=("fee_paise", "sum"),
            tax_sum_paise=("tax_paise", "sum"),
            settlement_utr=("settlement_utr", "first"),
            settled_at=("settled_at", "first"),
            entity_ids=("entity_id", list),
        )
        .reset_index()
    )

    grouped["expected_net_paise"] = (
        grouped["gross_sum_paise"]
        - grouped["fee_sum_paise"]
        - grouped["tax_sum_paise"]
    )

    return grouped.to_dict(orient="records")


def _nonempty_text(series: pd.Series) -> pd.Series:
    return series.notna() & series.astype(str).str.strip().ne("")


def reconcile(
    merchant_df: pd.DataFrame,
    razorpay_df: pd.DataFrame,
    bank_df: pd.DataFrame,
) -> ReconciliationResult:
    """Stage 1: deterministic validation, unique exact joins, and aggregation."""
    all_dead_letters: List[DeadLetterRow] = []

    merchant_rows, merchant_dead = _load_merchant_df(merchant_df)
    razorpay_rows, razorpay_dead = _load_razorpay_df(razorpay_df)
    bank_rows, bank_dead = _load_bank_df(bank_df)
    all_dead_letters.extend(merchant_dead)
    all_dead_letters.extend(razorpay_dead)
    all_dead_letters.extend(bank_dead)

    matched_ledger_razorpay: List[MatchedLedgerRazorpay] = []
    amount_mismatches: List[AmountMismatchRecord] = []
    unresolved_ledger: List[UnresolvedRecord] = []

    merchant_df_clean = pd.DataFrame([row.model_dump() for row in merchant_rows])
    razorpay_df_clean = pd.DataFrame([row.model_dump() for row in razorpay_rows])
    bank_df_clean = pd.DataFrame([row.model_dump() for row in bank_rows])

    # ---- Exact ledger -> Razorpay ----
    # Merchant orders reconcile against payment entities, not refunds or
    # adjustments. Refunds affect settlement arithmetic but are not alternate
    # candidates for the original sale amount.
    payment_df = razorpay_df_clean[
        razorpay_df_clean["transaction_entity"].eq("payment")
        & _nonempty_text(razorpay_df_clean["order_id"])
        & _nonempty_text(razorpay_df_clean["entity_id"])
    ].copy()

    payment_counts = payment_df["order_id"].value_counts()
    duplicate_payment_order_ids = set(payment_counts[payment_counts > 1].index)

    merchant_gateway_counts = merchant_df_clean["gateway_order_id"].value_counts(
        dropna=True
    )

    payment_by_order = {
        order_id: group.iloc[0]
        for order_id, group in payment_df.groupby("order_id", sort=False)
        if order_id not in duplicate_payment_order_ids and len(group) == 1
    }

    for _, merchant_row in merchant_df_clean.iterrows():
        merchant_order_id = merchant_row.get("merchant_order_id")
        gateway_order_id = merchant_row.get("gateway_order_id")

        if not gateway_order_id:
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=merchant_order_id or f"MERCHANT_ROW_{merchant_row.name}",
                    source="ledger",
                    reason="MISSING_GATEWAY_ORDER_ID",
                    context={
                        "gateway_order_id": None,
                        "gross_amount_paise": int(merchant_row["gross_amount_paise"]),
                    },
                )
            )
            continue

        if merchant_gateway_counts.get(gateway_order_id, 0) > 1:
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=merchant_order_id or gateway_order_id,
                    source="ledger",
                    reason="DUPLICATE_GATEWAY_ORDER_ID",
                    context={
                        "gateway_order_id": gateway_order_id,
                        "gross_amount_paise": int(merchant_row["gross_amount_paise"]),
                    },
                )
            )
            continue

        if gateway_order_id in duplicate_payment_order_ids:
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=merchant_order_id or gateway_order_id,
                    source="ledger",
                    reason="DUPLICATE_RAZORPAY_PAYMENT",
                    context={
                        "gateway_order_id": gateway_order_id,
                        "gross_amount_paise": int(merchant_row["gross_amount_paise"]),
                    },
                )
            )
            continue

        razorpay_row = payment_by_order.get(gateway_order_id)
        if razorpay_row is None:
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=merchant_order_id or gateway_order_id,
                    source="ledger",
                    reason="NO_EXACT_ORDER_ID",
                    context={
                        "gateway_order_id": gateway_order_id,
                        "gross_amount_paise": int(merchant_row["gross_amount_paise"]),
                    },
                )
            )
            continue

        merchant_amount = int(merchant_row["gross_amount_paise"])
        razorpay_amount = int(razorpay_row["amount_paise"])

        if merchant_amount == razorpay_amount:
            matched_ledger_razorpay.append(
                MatchedLedgerRazorpay(
                    merchant_order_id=merchant_order_id,
                    gateway_order_id=gateway_order_id,
                    amount_paise=merchant_amount,
                    match_method="EXACT_ORDER_ID",
                    razorpay_entity_id=razorpay_row["entity_id"],
                    razorpay_settlement_id=razorpay_row.get("settlement_id"),
                )
            )
        else:
            amount_mismatches.append(
                AmountMismatchRecord(
                    merchant_order_id=merchant_order_id,
                    gateway_order_id=gateway_order_id,
                    merchant_amount_paise=merchant_amount,
                    razorpay_amount_paise=razorpay_amount,
                    razorpay_entity_id=razorpay_row["entity_id"],
                    razorpay_settlement_id=razorpay_row.get("settlement_id"),
                )
            )

    # ---- Settlement aggregation ----
    settlement_agg = _aggregate_razorpay_by_settlement(razorpay_rows)
    settlement_agg_df = pd.DataFrame(settlement_agg)

    # Surface valid Razorpay rows which cannot belong to a settlement.
    for row in razorpay_rows:
        if not row.settlement_id:
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=row.entity_id,
                    source="razorpay",
                    reason="MISSING_SETTLEMENT_ID",
                    context={
                        "entity_id": row.entity_id,
                        "transaction_entity": row.transaction_entity,
                        "amount_paise": row.amount_paise,
                        "settlement_utr": row.settlement_utr,
                    },
                )
            )

    matched_settlements_bank: List[MatchedSettlementBank] = []
    settlement_amount_mismatches: List[SettlementAmountMismatch] = []
    unresolved_settlements: List[UnresolvedRecord] = []

    if not settlement_agg_df.empty:
        settlement_agg_df = settlement_agg_df[_nonempty_text(settlement_agg_df["settlement_id"])]

    bank_credit_df = bank_df_clean[
        _nonempty_text(bank_df_clean["utr"])
        & bank_df_clean["debit_paise"].eq(0)
        & bank_df_clean["credit_paise"].gt(0)
    ].copy()

    settlement_utr_counts = (
        settlement_agg_df["settlement_utr"].value_counts(dropna=True)
        if not settlement_agg_df.empty
        else pd.Series(dtype="int64")
    )
    bank_utr_counts = bank_credit_df["utr"].value_counts(dropna=True)

    bank_by_utr = {
        utr: group.iloc[0]
        for utr, group in bank_credit_df.groupby("utr", sort=False)
        if len(group) == 1
    }

    for _, settlement in settlement_agg_df.iterrows():
        settlement_id = settlement["settlement_id"]
        settlement_utr = settlement.get("settlement_utr")
        expected_net = int(settlement["expected_net_paise"])

        if not settlement_utr:
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=settlement_id,
                    source="razorpay",
                    reason="MISSING_SETTLEMENT_UTR",
                    context={
                        "settlement_utr": None,
                        "expected_net_paise": expected_net,
                        "settled_at": settlement.get("settled_at"),
                    },
                )
            )
            continue

        if settlement_utr_counts.get(settlement_utr, 0) > 1:
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=settlement_id,
                    source="razorpay",
                    reason="DUPLICATE_SETTLEMENT_UTR",
                    context={
                        "settlement_utr": settlement_utr,
                        "expected_net_paise": expected_net,
                        "settled_at": settlement.get("settled_at"),
                    },
                )
            )
            continue

        if bank_utr_counts.get(settlement_utr, 0) > 1:
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=settlement_id,
                    source="razorpay",
                    reason="DUPLICATE_BANK_UTR",
                    context={
                        "settlement_utr": settlement_utr,
                        "expected_net_paise": expected_net,
                        "settled_at": settlement.get("settled_at"),
                    },
                )
            )
            continue

        bank_row = bank_by_utr.get(settlement_utr)
        if bank_row is None:
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=settlement_id,
                    source="razorpay",
                    reason="NO_EXACT_UTR",
                    context={
                        "settlement_utr": settlement_utr,
                        "expected_net_paise": expected_net,
                        "settled_at": settlement.get("settled_at"),
                    },
                )
            )
            continue

        bank_credit = int(bank_row["credit_paise"])
        if expected_net == bank_credit:
            matched_settlements_bank.append(
                MatchedSettlementBank(
                    settlement_id=settlement_id,
                    settlement_utr=settlement_utr,
                    expected_net_paise=expected_net,
                    bank_credit_paise=bank_credit,
                    bank_reference=bank_row.get("utr"),
                    match_method="EXACT_UTR",
                )
            )
        else:
            settlement_amount_mismatches.append(
                SettlementAmountMismatch(
                    settlement_id=settlement_id,
                    settlement_utr=settlement_utr,
                    expected_net_paise=expected_net,
                    bank_credit_paise=bank_credit,
                    bank_reference=bank_row.get("utr"),
                )
            )

    summary = {
        "total_merchant_rows": len(merchant_df),
        "total_razorpay_rows": len(razorpay_df),
        "total_bank_rows": len(bank_df),
        "valid_merchant_rows": len(merchant_rows),
        "valid_razorpay_rows": len(razorpay_rows),
        "valid_bank_rows": len(bank_rows),
        "matched_ledger_razorpay_count": len(matched_ledger_razorpay),
        "amount_mismatch_count": len(amount_mismatches),
        "matched_settlements_bank_count": len(matched_settlements_bank),
        "settlement_amount_mismatch_count": len(settlement_amount_mismatches),
        "unresolved_ledger_count": len([r for r in unresolved_ledger if r.source == "ledger"]),
        "unresolved_razorpay_count": len([r for r in unresolved_ledger if r.source == "razorpay"]),
        "unresolved_settlement_count": len(unresolved_settlements),
        "dead_letter_count": len(all_dead_letters),
        "duplicate_razorpay_payment_order_count": len(duplicate_payment_order_ids),
    }

    return ReconciliationResult(
        matched_ledger_razorpay=matched_ledger_razorpay,
        amount_mismatches=amount_mismatches,
        matched_settlements_bank=matched_settlements_bank,
        settlement_amount_mismatches=settlement_amount_mismatches,
        unresolved_records=unresolved_ledger + unresolved_settlements,
        dead_letters=all_dead_letters,
        summary=summary,
    )


def _settlement_aggregation(razorpay_rows: List[CanonicalRazorpayRow]) -> List[Dict[str, Any]]:
    return _aggregate_razorpay_by_settlement(razorpay_rows)


def reconcile_full(
    merchant_df: pd.DataFrame,
    razorpay_df: pd.DataFrame,
    bank_df: pd.DataFrame,
) -> FullReconciliationResult:
    """Run Stage 1 + Stage 2 and return Stage 3 handoffs without calling Gemini."""
    stage1_result = reconcile(
        merchant_df=merchant_df,
        razorpay_df=razorpay_df,
        bank_df=bank_df,
    )

    merchant_rows, _ = _load_merchant_df(merchant_df)
    razorpay_rows, _ = _load_razorpay_df(razorpay_df)
    bank_rows, _ = _load_bank_df(bank_df)

    fuzzy_ledger_matches, fuzzy_settlement_matches, stage3_handoffs = reconcile_fuzzy(
        merchant_rows=merchant_rows,
        razorpay_rows=razorpay_rows,
        bank_rows=bank_rows,
        unresolved_from_stage1=stage1_result.unresolved_records,
    )

    stage3_response = [
        Stage3HandoffResponse(
            record=handoff["record"],
            status=handoff["status"],
            score=handoff["score"],
            amount_diff_paise=handoff["amount_diff_paise"],
            date_diff_days=handoff["date_diff_days"],
            error_code=handoff["error_code"],
            failed_gates=handoff["failed_gates"],
            reason=handoff["reason"],
            source_record_id=_sanitize_pandas_na(handoff.get("source_record_id")),
            candidate_record_id=_sanitize_pandas_na(handoff.get("candidate_record_id")),
            review_evidence=_sanitize_pandas_na(handoff.get("review_evidence")),
        )
        for handoff in stage3_handoffs
    ]

    # Backend is the authoritative owner of aggregate reconciliation counts.
    # Keep the individual collection counts for the frontend, but also expose
    # explicit aggregate link counts so the UI never has to add collections
    # itself.
    ledger_reconciled_link_count = (
        len(stage1_result.matched_ledger_razorpay)
        + len(fuzzy_ledger_matches)
    )
    settlement_bank_reconciled_link_count = (
        len(stage1_result.matched_settlements_bank)
        + len(fuzzy_settlement_matches)
    )
    backend_approved_match_count = (
        ledger_reconciled_link_count
        + settlement_bank_reconciled_link_count
    )

    summary = {
        **stage1_result.summary,
        "fuzzy_ledger_match_count": len(fuzzy_ledger_matches),
        "fuzzy_settlement_match_count": len(fuzzy_settlement_matches),
        "stage3_handoff_count": len(stage3_response),
        "ledger_reconciled_link_count": ledger_reconciled_link_count,
        "settlement_bank_reconciled_link_count": settlement_bank_reconciled_link_count,
        "backend_approved_match_count": backend_approved_match_count,
    }

    return FullReconciliationResult(
        matched_ledger_razorpay=stage1_result.matched_ledger_razorpay,
        fuzzy_ledger_matches=fuzzy_ledger_matches,
        amount_mismatches=stage1_result.amount_mismatches,
        matched_settlements_bank=stage1_result.matched_settlements_bank,
        fuzzy_settlement_matches=fuzzy_settlement_matches,
        settlement_amount_mismatches=stage1_result.settlement_amount_mismatches,
        stage3_handoffs=stage3_response,
        dead_letters=stage1_result.dead_letters,
        summary=summary,
    )
