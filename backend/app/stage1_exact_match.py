from typing import List, Dict, Any, Optional
import pandas as pd
from pydantic import ValidationError

from .cleaners import (
    normalize_amount_to_paise,
    parse_datetime_to_iso,
    normalize_identifier,
    get_bank_reference_or_utr,
)
from .schemas import (
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    CanonicalBankRow,
    DeadLetterRow,
    MatchedLedgerRazorpay,
    AmountMismatchRecord,
    MatchedSettlementBank,
    SettlementAmountMismatch,
    UnresolvedRecord,
    ReconciliationResult,
)


def _load_merchant_df(raw_df: pd.DataFrame) -> tuple[List[CanonicalMerchantRow], List[DeadLetterRow]]:
    """
    Validate and canonicalize merchant ledger rows.
    Returns (clean_rows, dead_letters).
    """
    clean_rows: List[CanonicalMerchantRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            # Map raw CSV columns to canonical field names expected by CanonicalMerchantRow
            mapped = {
                "merchant_order_id": row.get("merchant_order_id"),
                "gateway_order_id": row.get("gateway_order_id"),
                "gross_amount_paise": row.get("gross_amount"),
                "order_created_at": row.get("order_created_at"),
                "customer_reference": row.get("customer_reference"),
                "order_status": row.get("order_status"),
                "source_system": row.get("source_system"),
            }
            canonical = CanonicalMerchantRow(**mapped)
            clean_rows.append(canonical)
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="merchant",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(e),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _load_razorpay_df(raw_df: pd.DataFrame) -> tuple[List[CanonicalRazorpayRow], List[DeadLetterRow]]:
    """
    Validate and canonicalize Razorpay settlement reconciliation rows.
    Returns (clean_rows, dead_letters).
    """
    clean_rows: List[CanonicalRazorpayRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            mapped = {
                "transaction_entity": row.get("transaction_entity"),
                "entity_id": row.get("entity_id"),
                "amount_paise": row.get("amount"),
                "currency": row.get("currency"),
                "fee_paise": row.get("fee (exclusive tax)"),
                "tax_paise": row.get("tax"),
                "debit_paise": row.get("debit"),
                "credit_paise": row.get("credit"),
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
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="razorpay",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(e),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _load_bank_df(raw_df: pd.DataFrame) -> tuple[List[CanonicalBankRow], List[DeadLetterRow]]:
    """
    Validate and canonicalize bank statement rows.
    Returns (clean_rows, dead_letters).

    This function:
    - Uses get_bank_reference_or_utr to compute a canonical UTR from reference_number + description.
    - Populates the utr field explicitly after model construction.
    """
    clean_rows: List[CanonicalBankRow] = []
    dead_letters: List[DeadLetterRow] = []

    for idx, row in raw_df.iterrows():
        try:
            ref = row.get("reference_number")
            description = row.get("description")

            # Compute canonical UTR/reference using deterministic Stage 1 logic
            utr = get_bank_reference_or_utr(ref, description)

            mapped = {
                "transaction_date": row.get("transaction_date"),
                "value_date": row.get("value_date"),
                "description": description,
                "reference_number": ref,
                "debit_paise": row.get("debit"),
                "credit_paise": row.get("credit"),
                "balance_paise": row.get("balance"),
                "currency": row.get("currency"),
                # We'll set utr manually after validation, since the validator currently returns None
                "utr": utr,
            }

            canonical = CanonicalBankRow(**mapped)
            # Force utr to the computed value (validator currently ignores input)
            canonical.utr = utr

            clean_rows.append(canonical)
        except (ValidationError, ValueError, KeyError, TypeError) as e:
            dead_letters.append(
                DeadLetterRow(
                    row_index=int(idx),
                    source="bank",
                    error_code="SCHEMA_VALIDATION_FAILED",
                    error_message=str(e),
                    raw_row=row.to_dict(),
                )
            )

    return clean_rows, dead_letters


def _aggregate_razorpay_by_settlement(
    razorpay_rows: List[CanonicalRazorpayRow],
) -> List[Dict[str, Any]]:
    """
    Aggregate Razorpay rows by settlement_id.

    For each settlement_id:
    - Sum amount_paise, fee_paise, tax_paise.
    - Take first settlement_utr, settled_at.
    - Compute expected_net_paise = sum(amount) - sum(fee) - sum(tax).

    Returns a list of dicts suitable for building a DataFrame.
    """
    df = pd.DataFrame([r.model_dump() for r in razorpay_rows])

    if df.empty:
        return []

    grouped = (
        df.groupby("settlement_id", dropna=True)
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
    Stage 1 reconciliation:

    1. Validate and canonicalize all three sources.
    2. Exact join: merchant.gateway_order_id == razorpay.order_id, then verify amount.
    3. Aggregate Razorpay by settlement_id.
    4. Exact join: settlement_utr == bank.utr, then verify net amount.
    5. Return a ReconciliationResult with:
       - matched_ledger_razorpay
       - amount_mismatches
       - matched_settlements_bank
       - settlement_amount_mismatches
       - unresolved_records
       - dead_letters
       - summary
    """

    all_dead_letters: List[DeadLetterRow] = []

    # 1. Load and validate each source
    merchant_rows, merchant_dead = _load_merchant_df(merchant_df)
    razorpay_rows, razorpay_dead = _load_razorpay_df(razorpay_df)
    bank_rows, bank_dead = _load_bank_df(bank_df)

    all_dead_letters.extend(merchant_dead)
    all_dead_letters.extend(razorpay_dead)
    all_dead_letters.extend(bank_dead)

    # 2. Exact join: merchant <-> razorpay on gateway_order_id / order_id
    matched_ledger_razorpay: List[MatchedLedgerRazorpay] = []
    amount_mismatches: List[AmountMismatchRecord] = []
    unresolved_ledger: List[UnresolvedRecord] = []

    merchant_df_clean = pd.DataFrame([r.model_dump() for r in merchant_rows])
    razorpay_df_clean = pd.DataFrame([r.model_dump() for r in razorpay_rows])

    if not merchant_df_clean.empty and not razorpay_df_clean.empty:
        ledger_rz = merchant_df_clean.merge(
            razorpay_df_clean,
            left_on="gateway_order_id",
            right_on="order_id",
            how="inner",
            suffixes=("_ledger", "_rz"),
        )

        matched_order_ids = set()

        for _, row in ledger_rz.iterrows():
            gateway_id = row["gateway_order_id"]
            merchant_amt = int(row["gross_amount_paise"])
            rz_amt = int(row["amount_paise"])

            matched_order_ids.add(gateway_id)

            if merchant_amt == rz_amt:
                matched_ledger_razorpay.append(
                    MatchedLedgerRazorpay(
                        merchant_order_id=row.get("merchant_order_id"),
                        gateway_order_id=gateway_id,
                        amount_paise=merchant_amt,
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
                        merchant_amount_paise=merchant_amt,
                        razorpay_amount_paise=rz_amt,
                        razorpay_entity_id=row["entity_id"],
                        razorpay_settlement_id=row.get("settlement_id"),
                    )
                )

        # Unresolved merchant rows: those not matched on gateway_order_id
        all_merchant_ids = set(merchant_df_clean["gateway_order_id"].dropna().unique())
        unresolved_merchant_ids = all_merchant_ids - matched_order_ids

        # Build unresolved records for merchant side
        for _, row in merchant_df_clean.iterrows():
            gid = row["gateway_order_id"]
            if gid in unresolved_merchant_ids:
                unresolved_ledger.append(
                    UnresolvedRecord(
                        record_id=row["merchant_order_id"] or gid,
                        source="ledger",
                        reason="NO_EXACT_ORDER_ID",
                        context={"gateway_order_id": gid},
                    )
                )
    else:
        # If either side is empty, all non-dead rows are unresolved
        for _, row in merchant_df_clean.iterrows():
            unresolved_ledger.append(
                UnresolvedRecord(
                    record_id=row["merchant_order_id"] or row["gateway_order_id"],
                    source="ledger",
                    reason="NO_EXACT_ORDER_ID",
                    context={"gateway_order_id": row["gateway_order_id"]},
                )
            )

    # 3. Aggregate Razorpay by settlement_id
    settlement_agg = _aggregate_razorpay_by_settlement(razorpay_rows)
    settlement_agg_df = pd.DataFrame(settlement_agg) if settlement_agg else pd.DataFrame()

    # 4. Exact join: settlement <-> bank on UTR
    matched_settlements_bank: List[MatchedSettlementBank] = []
    settlement_amount_mismatches: List[SettlementAmountMismatch] = []
    unresolved_settlements: List[UnresolvedRecord] = []

    bank_df_clean = pd.DataFrame([r.model_dump() for r in bank_rows])

    if not settlement_agg_df.empty and not bank_df_clean.empty:
        settle_bank = settlement_agg_df.merge(
            bank_df_clean,
            left_on="settlement_utr",
            right_on="utr",
            how="inner",
            suffixes=("_settle", "_bank"),
        )

        matched_settlement_ids = set()

        for _, row in settle_bank.iterrows():
            sid = row["settlement_id"]
            expected_net = int(row["expected_net_paise"])
            bank_credit = int(row["credit_paise"])

            matched_settlement_ids.add(sid)

            if expected_net == bank_credit:
                matched_settlements_bank.append(
                    MatchedSettlementBank(
                        settlement_id=sid,
                        settlement_utr=row.get("settlement_utr"),
                        expected_net_paise=expected_net,
                        bank_credit_paise=bank_credit,
                        bank_reference=row.get("utr"),
                        match_method="EXACT_UTR",
                    )
                )
            else:
                settlement_amount_mismatches.append(
                    SettlementAmountMismatch(
                        settlement_id=sid,
                        settlement_utr=row.get("settlement_utr"),
                        expected_net_paise=expected_net,
                        bank_credit_paise=bank_credit,
                        bank_reference=row.get("utr"),
                    )
                )

        # Unresolved settlements: those not matched on UTR
        all_settlement_ids = set(settlement_agg_df["settlement_id"].unique())
        unresolved_settlement_ids = all_settlement_ids - matched_settlement_ids

        for _, row in settlement_agg_df.iterrows():
            sid = row["settlement_id"]
            if sid in unresolved_settlement_ids:
                unresolved_settlements.append(
                    UnresolvedRecord(
                        record_id=sid,
                        source="razorpay",
                        reason="NO_EXACT_UTR",
                        context={
                            "settlement_utr": row.get("settlement_utr"),
                            "expected_net_paise": int(row["expected_net_paise"]),
                        },
                    )
                )
    else:
        # If either side is empty, all settlements are unresolved
        for _, row in settlement_agg_df.iterrows():
            unresolved_settlements.append(
                UnresolvedRecord(
                    record_id=row["settlement_id"],
                    source="razorpay",
                    reason="NO_EXACT_UTR",
                    context={
                        "settlement_utr": row.get("settlement_utr"),
                        "expected_net_paise": int(row["expected_net_paise"]),
                    },
                )
            )

    # 5. Build summary
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
        "unresolved_ledger_count": len(unresolved_ledger),
        "unresolved_settlement_count": len(unresolved_settlements),
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