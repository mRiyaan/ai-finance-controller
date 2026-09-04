from typing import List, Dict, Any

from .schemas import FullReconciliationResult
import pandas as pd
from pydantic import ValidationError

from .cleaners import (
    get_bank_reference_or_utr,
    normalize_amount_to_paise,
)
from .schemas import (
    AmountMismatchRecord,
    CanonicalBankRow,
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    DeadLetterRow,
    MatchedLedgerRazorpay,
    MatchedSettlementBank,
    ReconciliationResult,
    SettlementAmountMismatch,
    UnresolvedRecord,
    FullReconciliationResult,
    Stage3HandoffResponse,
)
from .stage2_fuzzy_match import reconcile_fuzzy, Stage3Handoff

def _optional_amount_to_paise(value: Any) -> int:
    """
    Convert an optional raw rupee amount to paise.

    Missing or blank values are interpreted as zero.
    Non-blank invalid values still raise ValueError and become dead letters.
    """
    if value is None:
        return 0

    if isinstance(value, str) and value.strip() == "":
        return 0

    return normalize_amount_to_paise(value)

def _load_merchant_df(
    raw_df: pd.DataFrame,
) -> tuple[List[CanonicalMerchantRow], List[DeadLetterRow]]:
    """
    Validate and canonicalize merchant ledger rows.

    Raw gross_amount is converted to integer paise before the
    CanonicalMerchantRow is created.
    """
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

            canonical = CanonicalMerchantRow(**mapped)
            clean_rows.append(canonical)

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
    """
    Validate and canonicalize Razorpay reconciliation report rows.

    Raw Razorpay report amounts are converted to integer paise before
    the CanonicalRazorpayRow is created.
    """
    clean_rows: List[CanonicalRazorpayRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            mapped = {
                "transaction_entity": row.get("transaction_entity"),
                "entity_id": row.get("entity_id"),
                "amount_paise": normalize_amount_to_paise(
                    row.get("amount")
                ),
                "currency": row.get("currency"),
                "fee_paise": _optional_amount_to_paise(
                    row.get("fee (exclusive tax)")
                ),
                "tax_paise": _optional_amount_to_paise(
                    row.get("tax")
                ),
                "debit_paise": _optional_amount_to_paise(
                    row.get("debit")
                ),
                "credit_paise": _optional_amount_to_paise(
                    row.get("credit")
                ),
                "payment_method": row.get("payment_method"),
                "entity_created_at": row.get("entity_created_at"),
                "payment_captured_at": row.get("payment_captured_at"),
                "order_id": row.get("order_id"),
                "settlement_id": row.get("settlement_id"),
                "settled_at": row.get("settled_at"),
                "settlement_utr": row.get("settlement_utr"),
                "settled_by": row.get("settled_by"),
            }

            canonical = CanonicalRazorpayRow(**mapped)
            clean_rows.append(canonical)

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
    """
    Validate and canonicalize bank statement rows.

    - Converts debit, credit, and balance from raw rupee strings to paise.
    - Obtains UTR deterministically from reference_number first, otherwise
      extracts it from description/narration.
    """
    clean_rows: List[CanonicalBankRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            reference_number = row.get("reference_number")
            description = row.get("description")

            utr = get_bank_reference_or_utr(
                reference_number,
                description,
            )

            mapped = {
                "transaction_date": row.get("transaction_date"),
                "value_date": row.get("value_date"),
                "description": description,
                "reference_number": reference_number,
                "debit_paise": _optional_amount_to_paise(
                    row.get("debit")
                ),
                "credit_paise": _optional_amount_to_paise(
                    row.get("credit")
                ),
                "balance_paise": _optional_amount_to_paise(
                    row.get("balance")
                ),
                "currency": row.get("currency"),
                "utr": utr,
            }

            canonical = CanonicalBankRow(**mapped)
            clean_rows.append(canonical)

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
    """
    Aggregate Razorpay rows by settlement_id.

    expected_net_paise =
        sum(amount_paise) - sum(fee_paise) - sum(tax_paise)
    """
    dataframe = pd.DataFrame([row.model_dump() for row in razorpay_rows])

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


def reconcile(
    merchant_df: pd.DataFrame,
    razorpay_df: pd.DataFrame,
    bank_df: pd.DataFrame,
) -> ReconciliationResult:
    """
    Run Stage 1 deterministic reconciliation.

    1. Validate and canonicalize merchant, Razorpay, and bank rows.
    2. Exact-match ledger.gateway_order_id to Razorpay.order_id.
    3. Verify matching order amounts in paise.
    4. Aggregate Razorpay amounts, fees, and tax by settlement.
    5. Exact-match settlement UTR to bank UTR.
    6. Verify settlement net amount equals bank credit in paise.
    """
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

    merchant_df_clean = pd.DataFrame(
        [row.model_dump() for row in merchant_rows]
    )
    razorpay_df_clean = pd.DataFrame(
        [row.model_dump() for row in razorpay_rows]
    )

    
    # Exact join A: Merchant ledger ↔ Razorpay order ID

    if not merchant_df_clean.empty and not razorpay_df_clean.empty:
        ledger_rz = merchant_df_clean.merge(
            razorpay_df_clean,
            left_on="gateway_order_id",
            right_on="order_id",
            how="inner",
            suffixes=("_ledger", "_rz"),
        )

        matched_gateway_ids = set()

        for _, row in ledger_rz.iterrows():
            gateway_id = row["gateway_order_id"]
            merchant_amount = int(row["gross_amount_paise"])
            razorpay_amount = int(row["amount_paise"])

            matched_gateway_ids.add(gateway_id)

            if merchant_amount == razorpay_amount:
                matched_ledger_razorpay.append(
                    MatchedLedgerRazorpay(
                        merchant_order_id=row.get("merchant_order_id"),
                        gateway_order_id=gateway_id,
                        amount_paise=merchant_amount,
                        match_method="EXACT_ORDER_ID",
                        razorpay_entity_id=row["entity_id"],
                        razorpay_settlement_id=row.get("settlement_id"),
                    )
                )
            else:
                amount_mismatches.append(
                    AmountMismatchRecord(
                        merchant_order_id=row.get("merchant_order_id"),
                        gateway_order_id=gateway_id,
                        merchant_amount_paise=merchant_amount,
                        razorpay_amount_paise=razorpay_amount,
                        razorpay_entity_id=row["entity_id"],
                        razorpay_settlement_id=row.get("settlement_id"),
                    )
                )

        all_gateway_ids = set(
            merchant_df_clean["gateway_order_id"].dropna().unique()
        )
        unresolved_gateway_ids = all_gateway_ids - matched_gateway_ids

        for _, row in merchant_df_clean.iterrows():
            gateway_id = row["gateway_order_id"]

            if gateway_id in unresolved_gateway_ids:
                unresolved_ledger.append(
                    UnresolvedRecord(
                        record_id=row["merchant_order_id"] or gateway_id,
                        source="ledger",
                        reason="NO_EXACT_ORDER_ID",
                        context={
                            "gateway_order_id": gateway_id,
                            "gross_amount_paise": int(
                                row["gross_amount_paise"]
                            ),
                        },
                    )
                )
    else:
        for _, row in merchant_df_clean.iterrows():
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=row["merchant_order_id"]
                    or row["gateway_order_id"],
                    source="ledger",
                    reason="NO_EXACT_ORDER_ID",
                    context={
                        "gateway_order_id": row["gateway_order_id"],
                        "gross_amount_paise": int(
                            row["gross_amount_paise"]
                        ),
                    },
                )
            )

    
    # Aggregate Razorpay rows by settlement

    settlement_agg = _aggregate_razorpay_by_settlement(razorpay_rows)
    settlement_agg_df = (
        pd.DataFrame(settlement_agg)
        if settlement_agg
        else pd.DataFrame()
    )

    
    # Exact join B: Razorpay settlement UTR ↔ Bank UTR
    
    matched_settlements_bank: List[MatchedSettlementBank] = []
    settlement_amount_mismatches: List[SettlementAmountMismatch] = []
    unresolved_settlements: List[UnresolvedRecord] = []

    bank_df_clean = pd.DataFrame(
        [row.model_dump() for row in bank_rows]
    )

    if not settlement_agg_df.empty and not bank_df_clean.empty:
        settlement_bank = settlement_agg_df.merge(
            bank_df_clean,
            left_on="settlement_utr",
            right_on="utr",
            how="inner",
            suffixes=("_settlement", "_bank"),
        )

        matched_settlement_ids = set()

        for _, row in settlement_bank.iterrows():
            settlement_id = row["settlement_id"]
            expected_net_paise = int(row["expected_net_paise"])
            bank_credit_paise = int(row["credit_paise"])

            matched_settlement_ids.add(settlement_id)

            if expected_net_paise == bank_credit_paise:
                matched_settlements_bank.append(
                    MatchedSettlementBank(
                        settlement_id=settlement_id,
                        settlement_utr=row.get("settlement_utr"),
                        expected_net_paise=expected_net_paise,
                        bank_credit_paise=bank_credit_paise,
                        bank_reference=row.get("utr"),
                        match_method="EXACT_UTR",
                    )
                )
            else:
                settlement_amount_mismatches.append(
                    SettlementAmountMismatch(
                        settlement_id=settlement_id,
                        settlement_utr=row.get("settlement_utr"),
                        expected_net_paise=expected_net_paise,
                        bank_credit_paise=bank_credit_paise,
                        bank_reference=row.get("utr"),
                    )
                )

        all_settlement_ids = set(
            settlement_agg_df["settlement_id"].dropna().unique()
        )
        unresolved_settlement_ids = (
            all_settlement_ids - matched_settlement_ids
        )

        for _, row in settlement_agg_df.iterrows():
            settlement_id = row["settlement_id"]

            if settlement_id in unresolved_settlement_ids:
                unresolved_settlements.append(
                    UnresolvedRecord(
                        record_id=settlement_id,
                        source="razorpay",
                        reason="NO_EXACT_UTR",
                        context={
                            "settlement_utr": row.get("settlement_utr"),
                            "expected_net_paise": int(
                                row["expected_net_paise"]
                            ),
                            "settled_at": row.get("settled_at"),
                        },
                    )
                )
    else:
        for _, row in settlement_agg_df.iterrows():
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=row["settlement_id"],
                    source="razorpay",
                    reason="NO_EXACT_UTR",
                    context={
                        "settlement_utr": row.get("settlement_utr"),
                        "expected_net_paise": int(
                            row["expected_net_paise"]
                        ),
                        "settled_at": row.get("settled_at"),
                    },
                )
            )

    summary = {
        "total_merchant_rows": len(merchant_df),
        "total_razorpay_rows": len(razorpay_df),
        "total_bank_rows": len(bank_df),
        "valid_merchant_rows": len(merchant_rows),
        "valid_razorpay_rows": len(razorpay_rows),
        "valid_bank_rows": len(bank_rows),
        "matched_ledger_razorpay_count": len(
            matched_ledger_razorpay
        ),
        "amount_mismatch_count": len(amount_mismatches),
        "matched_settlements_bank_count": len(
            matched_settlements_bank
        ),
        "settlement_amount_mismatch_count": len(
            settlement_amount_mismatches
        ),
        "unresolved_ledger_count": len(unresolved_ledger),
        "unresolved_settlement_count": len(
            unresolved_settlements
        ),
        "dead_letter_count": len(all_dead_letters),
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
def reconcile_full(
    merchant_df: pd.DataFrame,
    razorpay_df: pd.DataFrame,
    bank_df: pd.DataFrame,
) -> FullReconciliationResult:
    """
    Run Stage 1 exact reconciliation and Stage 2 fuzzy reconciliation.

    Stage 3 is not called here. Remaining Stage 2 records are returned as
    enriched stage3_handoffs, including trusted source/candidate evidence.
    """
    stage1_result = reconcile(
        merchant_df=merchant_df,
        razorpay_df=razorpay_df,
        bank_df=bank_df,
    )

    merchant_rows, _ = _load_merchant_df(merchant_df)
    razorpay_rows, _ = _load_razorpay_df(razorpay_df)
    bank_rows, _ = _load_bank_df(bank_df)

    fuzzy_ledger_matches, fuzzy_settlement_matches, stage3_handoffs = (
        reconcile_fuzzy(
            merchant_rows=merchant_rows,
            razorpay_rows=razorpay_rows,
            bank_rows=bank_rows,
            unresolved_from_stage1=stage1_result.unresolved_records,
        )
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
            source_record_id=handoff["source_record_id"],
            candidate_record_id=handoff["candidate_record_id"],
            review_evidence=handoff["review_evidence"],
        )
        for handoff in stage3_handoffs
    ]

    summary = {
        **stage1_result.summary,
        "fuzzy_ledger_match_count": len(fuzzy_ledger_matches),
        "fuzzy_settlement_match_count": len(fuzzy_settlement_matches),
        "stage3_handoff_count": len(stage3_response),
    }

    return FullReconciliationResult(
        matched_ledger_razorpay=stage1_result.matched_ledger_razorpay,
        fuzzy_ledger_matches=fuzzy_ledger_matches,
        amount_mismatches=stage1_result.amount_mismatches,
        matched_settlements_bank=stage1_result.matched_settlements_bank,
        fuzzy_settlement_matches=fuzzy_settlement_matches,
        settlement_amount_mismatches=(
            stage1_result.settlement_amount_mismatches
        ),
        stage3_handoffs=stage3_response,
        dead_letters=stage1_result.dead_letters,
        summary=summary,
    )