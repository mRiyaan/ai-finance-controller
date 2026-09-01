from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict

import pandas as pd
from rapidfuzz import fuzz

from .cleaners import parse_datetime_to_iso
from .schemas import (
    CanonicalBankRow,
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    MatchedLedgerRazorpay,
    MatchedSettlementBank,
    UnresolvedRecord,
)


class Stage3Handoff(TypedDict):
    """
    Structured Stage 2 → Stage 3 handoff.

    `record` remains unchanged from Stage 1.
    The other fields contain deterministic Stage 2 evidence that the
    LLM may explain, but must not override or invent.
    """

    record: UnresolvedRecord
    status: Literal["POTENTIAL_FUZZY_MATCH", "EXCEPTION"]
    score: float
    amount_diff_paise: int
    date_diff_days: Optional[int]
    error_code: Optional[str]
    failed_gates: List[str]
    reason: str


# Stage 2 thresholds
ORDER_ID_SIMILARITY_THRESHOLD_AUTO = 0.85
ORDER_ID_SIMILARITY_THRESHOLD_FLOOR = 0.75

UTR_SIMILARITY_THRESHOLD_AUTO = 0.85
UTR_SIMILARITY_THRESHOLD_FLOOR = 0.75

AMOUNT_TOLERANCE_PAISE = 500
MAX_DATE_OFFSET_DAYS = 3


def _date_difference_days(
    first_date: Optional[str],
    second_date: Optional[str],
) -> Optional[int]:
    """
    Parse both dates through cleaners.py and calculate second - first
    in complete calendar days.

    Returns None if either date is missing or cannot be parsed.
    """
    first_iso = parse_datetime_to_iso(first_date)
    second_iso = parse_datetime_to_iso(second_date)

    if not first_iso or not second_iso:
        return None

    try:
        first_datetime = datetime.fromisoformat(first_iso)
        second_datetime = datetime.fromisoformat(second_iso)
    except (ValueError, TypeError):
        return None

    return (second_datetime - first_datetime).days


def _get_failed_gates(
    score: float,
    auto_threshold: float,
    floor_threshold: float,
    amount_diff_paise: int,
    date_diff_days: Optional[int],
) -> List[str]:
    """
    Return all failed Stage 2 gates in finance-review priority order.

    Order:
    1. AMOUNT_MISMATCH
    2. DATE_OFFSET_EXCEEDED
    3. LOW_FUZZY_SCORE
    4. BELOW_AUTO_MATCH_THRESHOLD

    The first item becomes the primary error_code in the Stage 3 handoff.
    """
    failed_gates: List[str] = []

    # Financial discrepancy first.
    if amount_diff_paise > AMOUNT_TOLERANCE_PAISE:
        failed_gates.append("AMOUNT_MISMATCH")

    # Timing discrepancy second.
    if (
        date_diff_days is not None
        and abs(date_diff_days) > MAX_DATE_OFFSET_DAYS
    ):
        failed_gates.append("DATE_OFFSET_EXCEEDED")

    # Identifier-confidence checks after financial/timing checks.
    if score < floor_threshold:
        failed_gates.append("LOW_FUZZY_SCORE")
    elif score < auto_threshold:
        failed_gates.append("BELOW_AUTO_MATCH_THRESHOLD")

    return failed_gates


def _other_exception_handoff(
    record: UnresolvedRecord,
    reason: str,
) -> Stage3Handoff:
    """
    Build a consistent handoff when Stage 2 cannot evaluate a real
    candidate score, amount difference, or date difference.
    """
    return {
        "record": record,
        "status": "EXCEPTION",
        "score": 0.0,
        "amount_diff_paise": 0,
        "date_diff_days": None,
        "error_code": "OTHER",
        "failed_gates": ["OTHER"],
        "reason": reason,
    }


def _build_candidate_handoff(
    record: UnresolvedRecord,
    score: float,
    amount_diff_paise: int,
    date_diff_days: Optional[int],
    auto_threshold: float,
    floor_threshold: float,
    candidate_label: str,
) -> Stage3Handoff:
    """
    Build a complete Stage 3 handoff after Stage 2 evaluated a candidate.

    A potential fuzzy match is allowed only where:
    - score is at least the floor but below auto-match threshold;
    - amount is inside the tolerance; and
    - date is inside the window, or unavailable.

    All other non-auto-matches become EXCEPTION and preserve every
    failed deterministic gate.
    """
    failed_gates = _get_failed_gates(
        score=score,
        auto_threshold=auto_threshold,
        floor_threshold=floor_threshold,
        amount_diff_paise=amount_diff_paise,
        date_diff_days=date_diff_days,
    )

    is_potential_match = (
        floor_threshold <= score < auto_threshold
        and amount_diff_paise <= AMOUNT_TOLERANCE_PAISE
        and (
            date_diff_days is None
            or abs(date_diff_days) <= MAX_DATE_OFFSET_DAYS
        )
    )

    if is_potential_match:
        return {
            "record": record,
            "status": "POTENTIAL_FUZZY_MATCH",
            "score": score,
            "amount_diff_paise": amount_diff_paise,
            "date_diff_days": date_diff_days,
            "error_code": None,
            "failed_gates": failed_gates,
            "reason": (
                f"{candidate_label} candidate satisfies amount and date "
                "gates but is below the fuzzy auto-match threshold."
            ),
        }

    primary_error_code = failed_gates[0] if failed_gates else "OTHER"

    return {
        "record": record,
        "status": "EXCEPTION",
        "score": score,
        "amount_diff_paise": amount_diff_paise,
        "date_diff_days": date_diff_days,
        "error_code": primary_error_code,
        "failed_gates": failed_gates or ["OTHER"],
        "reason": (
            f"{candidate_label} candidate failed one or more Stage 2 "
            "reconciliation gates."
        ),
    }


def fuzzy_match_order_id(
    unresolved_ledger: List[UnresolvedRecord],
    merchant_rows: List[CanonicalMerchantRow],
    razorpay_rows: List[CanonicalRazorpayRow],
) -> Tuple[List[MatchedLedgerRazorpay], List[Stage3Handoff]]:
    """
    Evaluate Stage 1 unresolved ledger records against Razorpay order IDs.

    Auto-match only when:
    - order-ID similarity >= 0.85;
    - amount difference <= 500 paise; and
    - date offset <= 3 days when both dates exist.

    Records in the 0.75–0.849 score band with valid amount/date gates
    are forwarded as POTENTIAL_FUZZY_MATCH. All other records become
    EXCEPTION handoffs.
    """
    new_matches: List[MatchedLedgerRazorpay] = []
    stage3_handoffs: List[Stage3Handoff] = []

    merchant_by_gateway_id: Dict[str, CanonicalMerchantRow] = {
        row.gateway_order_id: row
        for row in merchant_rows
        if row.gateway_order_id
    }

    razorpay_by_order_id: Dict[str, List[CanonicalRazorpayRow]] = {}
    for row in razorpay_rows:
        if row.order_id:
            razorpay_by_order_id.setdefault(row.order_id, []).append(row)

    for record in unresolved_ledger:
        if record.source != "ledger":
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Expected a ledger unresolved record for order-ID matching.",
                )
            )
            continue

        gateway_order_id = record.context.get("gateway_order_id")
        if not gateway_order_id:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Missing gateway_order_id in the Stage 1 unresolved context.",
                )
            )
            continue

        merchant_row = merchant_by_gateway_id.get(gateway_order_id)
        if merchant_row is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No canonical merchant row was found for gateway_order_id.",
                )
            )
            continue

        best_candidate: Optional[CanonicalRazorpayRow] = None
        best_score = 0.0

        for razorpay_order_id, candidates in razorpay_by_order_id.items():
            score = (
                fuzz.ratio(
                    gateway_order_id.upper(),
                    razorpay_order_id.upper(),
                )
                / 100.0
            )

            if score > best_score:
                best_score = score
                best_candidate = candidates[0]

        if best_candidate is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No Razorpay order-ID candidate was available for fuzzy matching.",
                )
            )
            continue

        amount_diff_paise = abs(
            merchant_row.gross_amount_paise - best_candidate.amount_paise
        )

        date_diff_days = _date_difference_days(
            merchant_row.order_created_at,
            best_candidate.payment_captured_at,
        )

        is_auto_match = (
            best_score >= ORDER_ID_SIMILARITY_THRESHOLD_AUTO
            and amount_diff_paise <= AMOUNT_TOLERANCE_PAISE
            and (
                date_diff_days is None
                or abs(date_diff_days) <= MAX_DATE_OFFSET_DAYS
            )
        )

        if is_auto_match:
            new_matches.append(
                MatchedLedgerRazorpay(
                    merchant_order_id=merchant_row.merchant_order_id,
                    gateway_order_id=gateway_order_id,
                    amount_paise=merchant_row.gross_amount_paise,
                    match_method="FUZZY_ORDER_ID",
                    razorpay_entity_id=best_candidate.entity_id,
                    razorpay_settlement_id=best_candidate.settlement_id,
                )
            )
            continue

        stage3_handoffs.append(
            _build_candidate_handoff(
                record=record,
                score=best_score,
                amount_diff_paise=amount_diff_paise,
                date_diff_days=date_diff_days,
                auto_threshold=ORDER_ID_SIMILARITY_THRESHOLD_AUTO,
                floor_threshold=ORDER_ID_SIMILARITY_THRESHOLD_FLOOR,
                candidate_label="Order-ID",
            )
        )

    return new_matches, stage3_handoffs


def fuzzy_match_settlement_utr(
    unresolved_settlements: List[UnresolvedRecord],
    settlement_agg: List[Dict[str, Any]],
    bank_rows: List[CanonicalBankRow],
) -> Tuple[List[MatchedSettlementBank], List[Stage3Handoff]]:
    """
    Evaluate Stage 1 unresolved settlements against bank UTRs/narrations.

    Each settlement UTR is compared to:
    - canonical bank.utr, if present;
    - bank.description as a fallback for truncated/messy narration.

    Auto-match only when:
    - UTR similarity >= 0.85;
    - amount difference <= 500 paise; and
    - date offset <= 3 days when both dates exist.
    """
    new_matches: List[MatchedSettlementBank] = []
    stage3_handoffs: List[Stage3Handoff] = []

    settlement_by_id: Dict[str, Dict[str, Any]] = {
        settlement["settlement_id"]: settlement
        for settlement in settlement_agg
        if settlement.get("settlement_id")
    }

    for record in unresolved_settlements:
        if record.source != "razorpay":
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Expected a Razorpay unresolved record for UTR matching.",
                )
            )
            continue

        settlement_id = record.record_id
        settlement = settlement_by_id.get(settlement_id)

        if settlement is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No settlement aggregate was found for the unresolved settlement.",
                )
            )
            continue

        settlement_utr = settlement.get("settlement_utr") or record.context.get(
            "settlement_utr"
        )
        expected_net_paise = settlement.get("expected_net_paise")
        settled_at = settlement.get("settled_at")

        if not settlement_utr or expected_net_paise is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Missing settlement UTR or expected net amount.",
                )
            )
            continue

        best_bank: Optional[CanonicalBankRow] = None
        best_score = 0.0

        for bank_row in bank_rows:
            # A settlement payout should correspond to a bank credit,
            # not a debit transaction.
            if bank_row.debit_paise != 0:
                continue

            candidate_texts = [
                text
                for text in [bank_row.utr, bank_row.description]
                if text
            ]

            for candidate_text in candidate_texts:
                score = (
                    fuzz.ratio(
                        settlement_utr.upper(),
                        candidate_text.upper(),
                    )
                    / 100.0
                )

                if score > best_score:
                    best_score = score
                    best_bank = bank_row

        if best_bank is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No bank credit candidate was available for fuzzy UTR matching.",
                )
            )
            continue

        amount_diff_paise = abs(
            int(expected_net_paise) - best_bank.credit_paise
        )

        date_diff_days = _date_difference_days(
            settled_at,
            best_bank.transaction_date,
        )

        is_auto_match = (
            best_score >= UTR_SIMILARITY_THRESHOLD_AUTO
            and amount_diff_paise <= AMOUNT_TOLERANCE_PAISE
            and (
                date_diff_days is None
                or abs(date_diff_days) <= MAX_DATE_OFFSET_DAYS
            )
        )

        if is_auto_match:
            new_matches.append(
                MatchedSettlementBank(
                    settlement_id=settlement_id,
                    settlement_utr=settlement_utr,
                    expected_net_paise=int(expected_net_paise),
                    bank_credit_paise=best_bank.credit_paise,
                    bank_reference=best_bank.utr or best_bank.reference_number,
                    match_method="FUZZY_UTR",
                )
            )
            continue

        stage3_handoffs.append(
            _build_candidate_handoff(
                record=record,
                score=best_score,
                amount_diff_paise=amount_diff_paise,
                date_diff_days=date_diff_days,
                auto_threshold=UTR_SIMILARITY_THRESHOLD_AUTO,
                floor_threshold=UTR_SIMILARITY_THRESHOLD_FLOOR,
                candidate_label="UTR",
            )
        )

    return new_matches, stage3_handoffs


def reconcile_fuzzy(
    merchant_rows: List[CanonicalMerchantRow],
    razorpay_rows: List[CanonicalRazorpayRow],
    bank_rows: List[CanonicalBankRow],
    unresolved_from_stage1: List[UnresolvedRecord],
) -> Tuple[
    List[MatchedLedgerRazorpay],
    List[MatchedSettlementBank],
    List[Stage3Handoff],
]:
    """
    Run Stage 2 fuzzy matching only on Stage 1 unresolved records.

    Returns:
    - fuzzy ledger ↔ Razorpay matches;
    - fuzzy settlement ↔ bank matches;
    - complete audited Stage 3 handoffs for every record not accepted.
    """
    unresolved_ledger = [
        record
        for record in unresolved_from_stage1
        if record.source == "ledger"
    ]
    unresolved_settlements = [
        record
        for record in unresolved_from_stage1
        if record.source == "razorpay"
    ]

    fuzzy_ledger_matches, stage3_from_ledger = fuzzy_match_order_id(
        unresolved_ledger=unresolved_ledger,
        merchant_rows=merchant_rows,
        razorpay_rows=razorpay_rows,
    )

    razorpay_df = pd.DataFrame(
        [row.model_dump() for row in razorpay_rows]
    )

    if razorpay_df.empty:
        settlement_agg: List[Dict[str, Any]] = []
    else:
        grouped = (
            razorpay_df.groupby("settlement_id", dropna=True)
            .agg(
                gross_sum_paise=("amount_paise", "sum"),
                fee_sum_paise=("fee_paise", "sum"),
                tax_sum_paise=("tax_paise", "sum"),
                settlement_utr=("settlement_utr", "first"),
                settled_at=("settled_at", "first"),
            )
            .reset_index()
        )

        grouped["expected_net_paise"] = (
            grouped["gross_sum_paise"]
            - grouped["fee_sum_paise"]
            - grouped["tax_sum_paise"]
        )

        settlement_agg = grouped.to_dict(orient="records")

    fuzzy_settlement_matches, stage3_from_settlement = (
        fuzzy_match_settlement_utr(
            unresolved_settlements=unresolved_settlements,
            settlement_agg=settlement_agg,
            bank_rows=bank_rows,
        )
    )

    return (
        fuzzy_ledger_matches,
        fuzzy_settlement_matches,
        stage3_from_ledger + stage3_from_settlement,
    )