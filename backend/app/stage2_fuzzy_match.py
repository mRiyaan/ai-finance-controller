from datetime import datetime
import math
from typing import Any, Dict, List, Literal, Optional, Tuple, TypedDict

from rapidfuzz import fuzz
import pandas as pd

from .cleaners import parse_datetime_to_iso
from .schemas import (
    CanonicalBankRow,
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    MatchedLedgerRazorpay,
    MatchedSettlementBank,
    UnresolvedRecord,
)


def _none_if_pandas_na(value: Any) -> Any:
    """Convert pandas/numpy NA scalars to None without altering real values."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


class Stage3Handoff(TypedDict):
    record: UnresolvedRecord
    status: Literal["POTENTIAL_FUZZY_MATCH", "EXCEPTION"]
    score: float
    amount_diff_paise: int
    date_diff_days: Optional[int]
    error_code: Optional[str]
    failed_gates: List[str]
    reason: str
    source_record_id: Optional[str]
    candidate_record_id: Optional[str]
    review_evidence: Dict[str, Any]


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
    """Return a conservative whole-day difference for reconciliation gating.

    The previous implementation used ``timedelta.days``, which truncates
    ``3 days 23 hours`` to ``3`` and could incorrectly pass a strict +/-3 day
    gate.  We round any non-zero fractional day away from zero so a partial day
    beyond a whole-day boundary cannot be treated as inside the boundary.
    The underlying comparison remains conservative while preserving the
    existing ``Optional[int]`` Stage 3 contract.
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

    total_seconds = (second_datetime - first_datetime).total_seconds()
    if total_seconds == 0:
        return 0

    whole_days_away = math.ceil(abs(total_seconds) / 86_400)
    return whole_days_away if total_seconds > 0 else -whole_days_away


def _date_gate_passes(date_diff_days: Optional[int]) -> bool:
    return date_diff_days is None or abs(date_diff_days) <= MAX_DATE_OFFSET_DAYS


def _amount_gate_passes(amount_diff_paise: int) -> bool:
    return amount_diff_paise <= AMOUNT_TOLERANCE_PAISE


def _get_failed_gates(
    score: float,
    auto_threshold: float,
    floor_threshold: float,
    amount_diff_paise: int,
    date_diff_days: Optional[int],
) -> List[str]:
    failed_gates: List[str] = []

    if not _amount_gate_passes(amount_diff_paise):
        failed_gates.append("AMOUNT_MISMATCH")

    if date_diff_days is not None and abs(date_diff_days) > MAX_DATE_OFFSET_DAYS:
        failed_gates.append("DATE_OFFSET_EXCEEDED")

    if score < floor_threshold:
        failed_gates.append("LOW_FUZZY_SCORE")
    elif score < auto_threshold:
        failed_gates.append("BELOW_AUTO_MATCH_THRESHOLD")

    return failed_gates


def _build_other_review_evidence(record: UnresolvedRecord) -> Dict[str, Any]:
    if record.source == "ledger":
        comparison_type = "LEDGER_TO_RAZORPAY"
        comparison_field = "merchant.gateway_order_id ↔ razorpay.order_id"
        exception_id = f"EXC-LEDGER-{record.record_id}"
        source_record = {
            "record_id": record.record_id,
            "gateway_order_id": _none_if_pandas_na(record.context.get("gateway_order_id")),
            "gross_amount_paise": _none_if_pandas_na(record.context.get("gross_amount_paise")),
        }
        review_lookup = {
            "merchant_csv_search": _none_if_pandas_na(record.record_id),
            "razorpay_csv_search": _none_if_pandas_na(record.context.get("gateway_order_id")),
            "bank_csv_search": None,
        }
    elif record.source == "razorpay":
        comparison_type = "SETTLEMENT_TO_BANK"
        comparison_field = "razorpay.settlement_utr ↔ bank.utr"
        exception_id = f"EXC-SETTLEMENT-{record.record_id}"
        source_record = {
            "settlement_id": _none_if_pandas_na(record.record_id),
            "settlement_utr": _none_if_pandas_na(record.context.get("settlement_utr")),
            "expected_net_paise": _none_if_pandas_na(record.context.get("expected_net_paise")),
        }
        review_lookup = {
            "merchant_csv_search": None,
            "razorpay_csv_search": _none_if_pandas_na(record.record_id),
            "bank_csv_search": _none_if_pandas_na(record.context.get("settlement_utr")),
        }
    else:
        comparison_type = "UNKNOWN"
        comparison_field = "unknown"
        exception_id = f"EXC-{record.source.upper()}-{record.record_id}"
        source_record = {"record_id": record.record_id, "source": record.source}
        review_lookup = {
            "merchant_csv_search": None,
            "razorpay_csv_search": None,
            "bank_csv_search": None,
        }

    return {
        "exception_id": exception_id,
        "comparison_type": comparison_type,
        "comparison_field": comparison_field,
        "source_record": source_record,
        "candidate_record": {},
        "comparison": {
            "similarity_score": 0.0,
            "amount_diff_paise": 0,
            "date_diff_days": None,
            "failed_gates": ["OTHER"],
        },
        "review_lookup": review_lookup,
    }


def _other_exception_handoff(record: UnresolvedRecord, reason: str) -> Stage3Handoff:
    return {
        "record": record,
        "status": "EXCEPTION",
        "score": 0.0,
        "amount_diff_paise": 0,
        "date_diff_days": None,
        "error_code": "OTHER",
        "failed_gates": ["OTHER"],
        "reason": reason,
        "source_record_id": _none_if_pandas_na(record.record_id),
        "candidate_record_id": None,
        "review_evidence": _build_other_review_evidence(record),
    }


def _build_candidate_handoff(
    record: UnresolvedRecord,
    score: float,
    amount_diff_paise: int,
    date_diff_days: Optional[int],
    auto_threshold: float,
    floor_threshold: float,
    candidate_label: str,
    source_record_id: Optional[str],
    candidate_record_id: Optional[str],
    review_evidence: Dict[str, Any],
) -> Stage3Handoff:
    failed_gates = _get_failed_gates(
        score=score,
        auto_threshold=auto_threshold,
        floor_threshold=floor_threshold,
        amount_diff_paise=amount_diff_paise,
        date_diff_days=date_diff_days,
    )
    review_evidence["comparison"] = {
        "similarity_score": score,
        "amount_diff_paise": amount_diff_paise,
        "date_diff_days": date_diff_days,
        "failed_gates": failed_gates,
    }

    is_potential_match = (
        floor_threshold <= score < auto_threshold
        and _amount_gate_passes(amount_diff_paise)
        and _date_gate_passes(date_diff_days)
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
                f"{candidate_label} candidate satisfies amount and date gates "
                "but is below the fuzzy auto-match threshold."
            ),
            "source_record_id": _none_if_pandas_na(source_record_id),
            "candidate_record_id": _none_if_pandas_na(candidate_record_id),
            "review_evidence": review_evidence,
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
        "source_record_id": _none_if_pandas_na(source_record_id),
        "candidate_record_id": _none_if_pandas_na(candidate_record_id),
        "review_evidence": review_evidence,
    }


def _build_ledger_review_evidence(
    record: UnresolvedRecord,
    merchant_row: CanonicalMerchantRow,
    razorpay_row: CanonicalRazorpayRow,
) -> Dict[str, Any]:
    return {
        "exception_id": f"EXC-LEDGER-{record.record_id}",
        "comparison_type": "LEDGER_TO_RAZORPAY",
        "comparison_field": "merchant.gateway_order_id ↔ razorpay.order_id",
        "source_record": {
            "merchant_order_id": _none_if_pandas_na(merchant_row.merchant_order_id),
            "gateway_order_id": _none_if_pandas_na(merchant_row.gateway_order_id),
            "gross_amount_paise": _none_if_pandas_na(merchant_row.gross_amount_paise),
            "order_created_at": _none_if_pandas_na(merchant_row.order_created_at),
        },
        "candidate_record": {
            "razorpay_entity_id": _none_if_pandas_na(razorpay_row.entity_id),
            "razorpay_order_id": _none_if_pandas_na(razorpay_row.order_id),
            "razorpay_amount_paise": _none_if_pandas_na(razorpay_row.amount_paise),
            "payment_captured_at": _none_if_pandas_na(razorpay_row.payment_captured_at),
            "settlement_id": _none_if_pandas_na(razorpay_row.settlement_id),
            "settlement_utr": _none_if_pandas_na(razorpay_row.settlement_utr),
        },
        "comparison": {},
        "review_lookup": {
            "merchant_csv_search": _none_if_pandas_na(
                merchant_row.merchant_order_id or merchant_row.gateway_order_id
            ),
            "razorpay_csv_search": _none_if_pandas_na(
                razorpay_row.entity_id or razorpay_row.order_id
            ),
            "bank_csv_search": None,
        },
    }


def _build_settlement_review_evidence(
    record: UnresolvedRecord,
    settlement: Dict[str, Any],
    bank_row: CanonicalBankRow,
    bank_row_index: int,
) -> Dict[str, Any]:
    settlement_id = settlement.get("settlement_id") or record.record_id
    settlement_utr = settlement.get("settlement_utr") or record.context.get("settlement_utr")
    bank_reference = _none_if_pandas_na(bank_row.utr or bank_row.reference_number)

    return {
        "exception_id": f"EXC-SETTLEMENT-{settlement_id}",
        "comparison_type": "SETTLEMENT_TO_BANK",
        "comparison_field": "razorpay.settlement_utr ↔ bank.utr",
        "source_record": {
            "settlement_id": _none_if_pandas_na(settlement_id),
            "settlement_utr": _none_if_pandas_na(settlement_utr),
            "expected_net_paise": _none_if_pandas_na(settlement.get("expected_net_paise")),
            "settled_at": _none_if_pandas_na(settlement.get("settled_at")),
        },
        "candidate_record": {
            "bank_row_index": bank_row_index,
            "bank_reference_number": bank_reference,
            "bank_utr": _none_if_pandas_na(bank_row.utr),
            "bank_credit_paise": _none_if_pandas_na(bank_row.credit_paise),
            "bank_transaction_date": _none_if_pandas_na(bank_row.transaction_date),
            "bank_value_date": _none_if_pandas_na(bank_row.value_date),
        },
        "comparison": {},
        "review_lookup": {
            "merchant_csv_search": None,
            "razorpay_csv_search": _none_if_pandas_na(settlement_id),
            "bank_csv_search": bank_reference,
        },
    }


def _similarity(left: str, right: str) -> float:
    """Use ratio plus partial-ratio so embedded/truncated bank narration is comparable."""
    left_norm = left.strip().upper()
    right_norm = right.strip().upper()
    if not left_norm or not right_norm:
        return 0.0

    return max(
        fuzz.ratio(left_norm, right_norm),
        fuzz.partial_ratio(left_norm, right_norm),
    ) / 100.0


def _ledger_candidate_evaluations(
    merchant_row: CanonicalMerchantRow,
    candidates: List[CanonicalRazorpayRow],
) -> List[Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]]:
    evaluations: List[Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]] = []
    for candidate in candidates:
        score = _similarity(merchant_row.gateway_order_id or "", candidate.order_id or "")
        amount_diff = abs(merchant_row.gross_amount_paise - candidate.amount_paise)
        date_diff = _date_difference_days(
            merchant_row.order_created_at,
            candidate.payment_captured_at,
        )
        financially_eligible = _amount_gate_passes(amount_diff) and _date_gate_passes(date_diff)
        evaluations.append((candidate, score, amount_diff, date_diff, financially_eligible))
    return evaluations


def _choose_ledger_candidate(
    merchant_row: CanonicalMerchantRow,
    candidates: List[CanonicalRazorpayRow],
) -> Optional[Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]]:
    """Choose the best financially/date-eligible candidate, otherwise the best textual candidate."""
    evaluations = _ledger_candidate_evaluations(merchant_row, candidates)
    if not evaluations:
        return None
    eligible = [item for item in evaluations if item[4]]
    pool = eligible if eligible else evaluations
    return max(
        pool,
        key=lambda item: (
            item[1],
            -item[2],
            -(abs(item[3]) if item[3] is not None else 10_000),
            item[0].entity_id,
        ),
    )


def _assign_unique_ledger_candidates(
    records: List[UnresolvedRecord],
    merchant_by_gateway_id: Dict[str, CanonicalMerchantRow],
    candidates: List[CanonicalRazorpayRow],
) -> Dict[str, Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]]:
    """Assign at most one Razorpay payment entity to each unresolved ledger record.

    Phase 1 reserves financially/date-eligible candidates first, globally by best
    similarity. Phase 2 assigns remaining unused candidates for exception evidence.
    This prevents one Razorpay payment from being presented as the selected
    candidate for multiple merchant records.
    """
    evaluations_by_record: Dict[str, List[Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]]] = {}
    for record in records:
        if record.source != "ledger":
            continue
        gateway_id = record.context.get("gateway_order_id")
        merchant_row = merchant_by_gateway_id.get(gateway_id)
        if merchant_row is None:
            continue
        evaluations_by_record[record.record_id] = _ledger_candidate_evaluations(
            merchant_row, candidates
        )

    assignments: Dict[str, Tuple[CanonicalRazorpayRow, float, int, Optional[int], bool]] = {}
    used_entities: set[str] = set()

    # Prefer candidates that actually pass financial/date gates. Sort globally so
    # a candidate is allocated to the strongest eligible record rather than the
    # first row encountered in the source CSV.
    eligible_claims: List[Tuple[float, int, int, str, CanonicalRazorpayRow, bool]] = []
    for record_id, evaluations in evaluations_by_record.items():
        for candidate, score, amount_diff, date_diff, gates_pass in evaluations:
            if gates_pass:
                eligible_claims.append(
                    (
                        score,
                        -amount_diff,
                        -(abs(date_diff) if date_diff is not None else 10_000),
                        record_id,
                        candidate,
                        gates_pass,
                    )
                )

    eligible_claims.sort(
        key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4].entity_id)
    )
    assigned_records: set[str] = set()

    for score, neg_amount_diff, neg_date_diff, record_id, candidate, gates_pass in eligible_claims:
        if record_id in assigned_records or candidate.entity_id in used_entities:
            continue
        chosen = next(
            item
            for item in evaluations_by_record[record_id]
            if item[0].entity_id == candidate.entity_id
        )
        assignments[record_id] = chosen
        assigned_records.add(record_id)
        used_entities.add(candidate.entity_id)

    # Remaining records can receive an unused best-text candidate solely as
    # review context. It is still one-to-one and never overrides financial gates.
    for record_id, evaluations in sorted(evaluations_by_record.items()):
        if record_id in assigned_records:
            continue
        available = [item for item in evaluations if item[0].entity_id not in used_entities]
        if not available:
            continue
        chosen = max(
            available,
            key=lambda item: (
                item[1],
                item[4],
                -item[2],
                -(abs(item[3]) if item[3] is not None else 10_000),
                item[0].entity_id,
            ),
        )
        assignments[record_id] = chosen
        assigned_records.add(record_id)
        used_entities.add(chosen[0].entity_id)

    return assignments


def fuzzy_match_order_id(
    unresolved_ledger: List[UnresolvedRecord],
    merchant_rows: List[CanonicalMerchantRow],
    razorpay_rows: List[CanonicalRazorpayRow],
) -> Tuple[List[MatchedLedgerRazorpay], List[Stage3Handoff]]:
    new_matches: List[MatchedLedgerRazorpay] = []
    stage3_handoffs: List[Stage3Handoff] = []

    merchant_by_gateway_id: Dict[str, CanonicalMerchantRow] = {
        row.gateway_order_id: row
        for row in merchant_rows
        if row.gateway_order_id
    }

    razorpay_by_order_id: Dict[str, List[CanonicalRazorpayRow]] = {}
    for row in razorpay_rows:
        if row.transaction_entity != "payment" or not row.order_id:
            continue
        razorpay_by_order_id.setdefault(row.order_id, []).append(row)

    razorpay_by_unique_order: Dict[str, List[CanonicalRazorpayRow]] = {
        order_id: rows
        for order_id, rows in razorpay_by_order_id.items()
        if len(rows) == 1
    }

    unresolved_gateway_ids = {
        record.context.get("gateway_order_id")
        for record in unresolved_ledger
        if record.source == "ledger"
    }
    unresolved_gateway_ids.discard(None)
    exact_consumed_order_ids = set(merchant_by_gateway_id) - unresolved_gateway_ids

    candidate_rows = [
        rows[0]
        for order_id, rows in razorpay_by_unique_order.items()
        if order_id not in exact_consumed_order_ids
    ]

    assignments = _assign_unique_ledger_candidates(
        records=unresolved_ledger,
        merchant_by_gateway_id=merchant_by_gateway_id,
        candidates=candidate_rows,
    )

    for record in unresolved_ledger:
        if record.source != "ledger":
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Expected a ledger unresolved record for order-ID matching.",
                )
            )
            continue

        gateway_order_id = _none_if_pandas_na(record.context.get("gateway_order_id"))
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

        selected = assignments.get(record.record_id)
        if selected is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No unallocated unique Razorpay payment candidate was available for this unresolved ledger record.",
                )
            )
            continue

        best_candidate, best_score, amount_diff_paise, date_diff_days, gates_pass = selected

        if best_score >= ORDER_ID_SIMILARITY_THRESHOLD_AUTO and gates_pass:
            new_matches.append(
                MatchedLedgerRazorpay(
                    merchant_order_id=merchant_row.merchant_order_id,
                    gateway_order_id=str(gateway_order_id),
                    amount_paise=merchant_row.gross_amount_paise,
                    match_method="FUZZY_ORDER_ID",
                    razorpay_entity_id=best_candidate.entity_id,
                    razorpay_settlement_id=best_candidate.settlement_id,
                )
            )
            continue

        review_evidence = _build_ledger_review_evidence(record, merchant_row, best_candidate)
        stage3_handoffs.append(
            _build_candidate_handoff(
                record=record,
                score=best_score,
                amount_diff_paise=amount_diff_paise,
                date_diff_days=date_diff_days,
                auto_threshold=ORDER_ID_SIMILARITY_THRESHOLD_AUTO,
                floor_threshold=ORDER_ID_SIMILARITY_THRESHOLD_FLOOR,
                candidate_label="Order-ID",
                source_record_id=merchant_row.merchant_order_id or gateway_order_id,
                candidate_record_id=best_candidate.entity_id or best_candidate.order_id,
                review_evidence=review_evidence,
            )
        )

    return new_matches, stage3_handoffs


def _bank_candidate_evaluations(
    settlement: Dict[str, Any],
    bank_rows: List[Tuple[int, CanonicalBankRow]],
) -> List[Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]]:
    settlement_utr = str(settlement.get("settlement_utr") or "")
    expected_net_paise = int(settlement.get("expected_net_paise") or 0)
    settled_at = settlement.get("settled_at")

    evaluations: List[Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]] = []
    for bank_index, bank_row in bank_rows:
        if bank_row.debit_paise != 0 or bank_row.credit_paise <= 0:
            continue

        text_candidates = [text for text in [bank_row.utr, bank_row.description] if text]
        score = max(
            (_similarity(settlement_utr, text) for text in text_candidates),
            default=0.0,
        )
        amount_diff = abs(expected_net_paise - bank_row.credit_paise)
        date_diff = _date_difference_days(settled_at, bank_row.transaction_date)
        financially_eligible = _amount_gate_passes(amount_diff) and _date_gate_passes(date_diff)
        evaluations.append(
            (bank_row, bank_index, score, amount_diff, date_diff, financially_eligible)
        )
    return evaluations


def _choose_bank_candidate(
    settlement: Dict[str, Any],
    bank_rows: List[Tuple[int, CanonicalBankRow]],
) -> Optional[Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]]:
    evaluations = _bank_candidate_evaluations(settlement, bank_rows)
    if not evaluations:
        return None
    eligible = [item for item in evaluations if item[5]]
    pool = eligible if eligible else evaluations
    return max(
        pool,
        key=lambda item: (
            item[2],
            -item[3],
            -(abs(item[4]) if item[4] is not None else 10_000),
            -item[1],
        ),
    )


def _assign_unique_bank_candidates(
    records: List[UnresolvedRecord],
    settlement_by_id: Dict[str, Dict[str, Any]],
    candidate_bank_rows: List[Tuple[int, CanonicalBankRow]],
) -> Dict[str, Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]]:
    """Assign at most one unique bank row to each unresolved settlement."""
    evaluations_by_record: Dict[str, List[Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]]] = {}
    for record in records:
        if record.source != "razorpay":
            continue
        settlement = settlement_by_id.get(record.record_id)
        if settlement is None:
            continue
        if not settlement.get("settlement_utr") or settlement.get("expected_net_paise") is None:
            continue
        evaluations_by_record[record.record_id] = _bank_candidate_evaluations(
            settlement, candidate_bank_rows
        )

    assignments: Dict[str, Tuple[CanonicalBankRow, int, float, int, Optional[int], bool]] = {}
    used_indices: set[int] = set()
    assigned_records: set[str] = set()

    eligible_claims: List[Tuple[float, int, int, str, int, CanonicalBankRow]] = []
    for record_id, evaluations in evaluations_by_record.items():
        for bank_row, bank_index, score, amount_diff, date_diff, gates_pass in evaluations:
            if gates_pass:
                eligible_claims.append(
                    (
                        score,
                        -amount_diff,
                        -(abs(date_diff) if date_diff is not None else 10_000),
                        record_id,
                        bank_index,
                        bank_row,
                    )
                )

    eligible_claims.sort(
        key=lambda item: (-item[0], -item[1], -item[2], item[3], item[4])
    )

    for score, neg_amount_diff, neg_date_diff, record_id, bank_index, bank_row in eligible_claims:
        if record_id in assigned_records or bank_index in used_indices:
            continue
        chosen = next(
            item
            for item in evaluations_by_record[record_id]
            if item[1] == bank_index
        )
        assignments[record_id] = chosen
        assigned_records.add(record_id)
        used_indices.add(bank_index)

    for record_id, evaluations in sorted(evaluations_by_record.items()):
        if record_id in assigned_records:
            continue
        available = [item for item in evaluations if item[1] not in used_indices]
        if not available:
            continue
        chosen = max(
            available,
            key=lambda item: (
                item[2],
                item[5],
                -item[3],
                -(abs(item[4]) if item[4] is not None else 10_000),
                -item[1],
            ),
        )
        assignments[record_id] = chosen
        assigned_records.add(record_id)
        used_indices.add(chosen[1])

    return assignments


def fuzzy_match_settlement_utr(
    unresolved_settlements: List[UnresolvedRecord],
    settlement_agg: List[Dict[str, Any]],
    bank_rows: List[CanonicalBankRow],
) -> Tuple[List[MatchedSettlementBank], List[Stage3Handoff]]:
    new_matches: List[MatchedSettlementBank] = []
    stage3_handoffs: List[Stage3Handoff] = []

    settlement_by_id: Dict[str, Dict[str, Any]] = {
        settlement["settlement_id"]: settlement
        for settlement in settlement_agg
        if settlement.get("settlement_id")
    }

    bank_utr_counts: Dict[str, int] = {}
    for bank_row in bank_rows:
        if bank_row.utr:
            bank_utr_counts[bank_row.utr] = bank_utr_counts.get(bank_row.utr, 0) + 1

    candidate_bank_rows = [
        (index, row)
        for index, row in enumerate(bank_rows)
        if (
            row.utr
            and bank_utr_counts.get(row.utr, 0) == 1
            and row.debit_paise == 0
            and row.credit_paise > 0
        )
    ]

    duplicate_utr_records: set[str] = set()
    for record in unresolved_settlements:
        if record.source != "razorpay":
            continue
        settlement = settlement_by_id.get(record.record_id)
        if settlement is None:
            continue
        settlement_utr = _none_if_pandas_na(
            settlement.get("settlement_utr") or record.context.get("settlement_utr")
        )
        if settlement_utr:
            normalized_utr = str(settlement_utr).strip().upper()
            if bank_utr_counts.get(normalized_utr, 0) > 1:
                duplicate_utr_records.add(record.record_id)

    assignable_unresolved_settlements = [
        record
        for record in unresolved_settlements
        if record.record_id not in duplicate_utr_records
    ]

    assignments = _assign_unique_bank_candidates(
        records=assignable_unresolved_settlements,
        settlement_by_id=settlement_by_id,
        candidate_bank_rows=candidate_bank_rows,
    )

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

        settlement_utr = _none_if_pandas_na(
            settlement.get("settlement_utr") or record.context.get("settlement_utr")
        )
        expected_net_paise = settlement.get("expected_net_paise")

        if not settlement_utr or expected_net_paise is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "Missing settlement UTR or expected net amount.",
                )
            )
            continue

        normalized_utr = str(settlement_utr).strip().upper()
        if settlement_id in duplicate_utr_records:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    (
                        "DUPLICATE_BANK_UTR: settlement UTR appears more than once "
                        "in the bank statement; no bank row was selected to avoid "
                        "misattributing an ambiguous payout."
                    ),
                )
            )
            continue

        selected = assignments.get(settlement_id)
        if selected is None:
            stage3_handoffs.append(
                _other_exception_handoff(
                    record,
                    "No unallocated unique bank credit candidate was available for this unresolved settlement.",
                )
            )
            continue

        best_bank, best_bank_index, best_score, amount_diff_paise, date_diff_days, gates_pass = selected
        review_evidence = _build_settlement_review_evidence(
            record=record,
            settlement=settlement,
            bank_row=best_bank,
            bank_row_index=best_bank_index,
        )

        handoff = _build_candidate_handoff(
            record=record,
            score=best_score,
            amount_diff_paise=amount_diff_paise,
            date_diff_days=date_diff_days,
            auto_threshold=UTR_SIMILARITY_THRESHOLD_AUTO,
            floor_threshold=UTR_SIMILARITY_THRESHOLD_FLOOR,
            candidate_label="UTR",
            source_record_id=settlement_id,
            candidate_record_id=best_bank.utr or best_bank.reference_number,
            review_evidence=review_evidence,
        )

        if best_score >= UTR_SIMILARITY_THRESHOLD_AUTO and gates_pass:
            new_matches.append(
                MatchedSettlementBank(
                    settlement_id=settlement_id,
                    settlement_utr=str(settlement_utr),
                    expected_net_paise=int(expected_net_paise),
                    bank_credit_paise=best_bank.credit_paise,
                    bank_reference=best_bank.utr or best_bank.reference_number,
                    match_method="FUZZY_UTR",
                )
            )
        else:
            stage3_handoffs.append(handoff)

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
    unresolved_ledger = [
        record for record in unresolved_from_stage1 if record.source == "ledger"
    ]
    unresolved_settlements = [
        record for record in unresolved_from_stage1 if record.source == "razorpay"
    ]

    fuzzy_ledger_matches, stage3_from_ledger = fuzzy_match_order_id(
        unresolved_ledger=unresolved_ledger,
        merchant_rows=merchant_rows,
        razorpay_rows=razorpay_rows,
    )

    razorpay_df = [row.model_dump() for row in razorpay_rows]
    settlement_agg: List[Dict[str, Any]] = []

    if razorpay_df:
        dataframe = pd.DataFrame(razorpay_df)
        dataframe = dataframe[
            dataframe["settlement_id"].notna()
            & dataframe["settlement_id"].astype(str).str.strip().ne("")
        ]
        if not dataframe.empty:
            grouped = (
                dataframe.groupby("settlement_id", dropna=True)
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

    fuzzy_settlement_matches, stage3_from_settlement = fuzzy_match_settlement_utr(
        unresolved_settlements=unresolved_settlements,
        settlement_agg=settlement_agg,
        bank_rows=bank_rows,
    )

    return (
        fuzzy_ledger_matches,
        fuzzy_settlement_matches,
        stage3_from_ledger + stage3_from_settlement,
    )
