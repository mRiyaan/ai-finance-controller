import pytest
from typing import List, Dict, Any
from app.schemas import (
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    CanonicalBankRow,
    UnresolvedRecord,
    MatchedLedgerRazorpay,
    MatchedSettlementBank,
)
from app.stage2_fuzzy_match import (
    _date_difference_days,
    fuzzy_match_order_id,
    fuzzy_match_settlement_utr,
    reconcile_fuzzy,
    Stage3Handoff,
)


class TestFuzzyOrderIdMatching:
    def test_fuzzy_match_with_minor_typo(self):
        merchant_rows = [
            CanonicalMerchantRow(
                merchant_order_id="INV-001",
                gateway_order_id="ORDER_ABC123X",
                gross_amount_paise=100000,
                order_created_at="2026-08-27T10:00:00",
            )
        ]
        razorpay_rows = [
            CanonicalRazorpayRow(
                transaction_entity="payment",
                entity_id="PAY_ABC",
                amount_paise=100000,
                currency="INR",
                order_id="ORDER_ABC123",
                settlement_id="SETL_001",
                payment_captured_at="2026-08-27T10:05:00",
            ),
        ]
        unresolved = [
            UnresolvedRecord(
                record_id="INV-001",
                source="ledger",
                reason="NO_EXACT_ORDER_ID",
                context={"gateway_order_id": "ORDER_ABC123X"},
            )
        ]

        matches, stage3 = fuzzy_match_order_id(unresolved, merchant_rows, razorpay_rows)

        # With a minor typo, score should be high; amount/date OK → auto-match
        assert len(matches) == 1
        assert matches[0].gateway_order_id == "ORDER_ABC123X"
        assert matches[0].match_method == "FUZZY_ORDER_ID"
        assert len(stage3) == 0

    def test_no_fuzzy_match_when_similarity_too_low(self):
        merchant_rows = [
            CanonicalMerchantRow(
                merchant_order_id="INV-001",
                gateway_order_id="ORDER_XYZ999",
                gross_amount_paise=100000,
                order_created_at="2026-08-27T10:00:00",
            )
        ]
        razorpay_rows = [
            CanonicalRazorpayRow(
                transaction_entity="payment",
                entity_id="PAY_ABC",
                amount_paise=100000,
                currency="INR",
                order_id="ORDER_TOTALLYDIFFERENT",
                settlement_id="SETL_001",
                payment_captured_at="2026-08-27T10:05:00",
            ),
        ]
        unresolved = [
            UnresolvedRecord(
                record_id="INV-001",
                source="ledger",
                reason="NO_EXACT_ORDER_ID",
                context={"gateway_order_id": "ORDER_XYZ999"},
            )
        ]

        matches, stage3 = fuzzy_match_order_id(unresolved, merchant_rows, razorpay_rows)

        assert len(matches) == 0
        # Low similarity → no auto-match; record goes to Stage 3 as EXCEPTION
        assert len(stage3) == 1
        assert stage3[0]["record"].record_id == "INV-001"
        assert stage3[0]["status"] == "EXCEPTION"
        assert stage3[0]["error_code"] == "LOW_FUZZY_SCORE"

    def test_amount_tolerance_check(self):
        # Similar IDs but amount differs by more than tolerance (500 paise)
        merchant_rows = [
            CanonicalMerchantRow(
                merchant_order_id="INV-001",
                gateway_order_id="ORDER_ABC123X",
                gross_amount_paise=100000,
                order_created_at="2026-08-27T10:00:00",
            )
        ]
        razorpay_rows = [
            CanonicalRazorpayRow(
                transaction_entity="payment",
                entity_id="PAY_ABC",
                amount_paise=110000,  # 10,000 paise difference
                currency="INR",
                order_id="ORDER_ABC123",
                settlement_id="SETL_001",
                payment_captured_at="2026-08-27T10:05:00",
            ),
        ]
        unresolved = [
            UnresolvedRecord(
                record_id="INV-001",
                source="ledger",
                reason="NO_EXACT_ORDER_ID",
                context={"gateway_order_id": "ORDER_ABC123X"},
            )
        ]

        matches, stage3 = fuzzy_match_order_id(unresolved, merchant_rows, razorpay_rows)

        assert len(matches) == 0  # rejected due to amount difference
        # Goes to Stage 3 as EXCEPTION (amount mismatch)
        assert len(stage3) == 1
        assert stage3[0]["record"].record_id == "INV-001"
        assert stage3[0]["status"] == "EXCEPTION"
        assert stage3[0]["error_code"] == "AMOUNT_MISMATCH"


class TestFuzzySettlementUTRMatching:
    def test_utr_handoff_includes_all_failed_gates(self):
        settlement_agg: List[Dict[str, Any]] = [
            {
                "settlement_id": "SETL_001",
                "settlement_utr": "HDFCINBB2026800222",
                "expected_net_paise": 97640,
                "settled_at": "2026-08-20T14:00:00",
            }
        ]

        bank_rows = [
            CanonicalBankRow(
                transaction_date="2026-08-29T00:00:00",
                value_date="2026-08-29T00:00:00",
                description="NEFT CR RAZORPAY SETTLEMENT HDFCINBB202680022",
                reference_number="",
                debit_paise=0,
                credit_paise=110000,
                balance_paise=100000000,
                currency="INR",
                utr="HDFCINBB202680022",
            )
        ]

        unresolved = [
            UnresolvedRecord(
                record_id="SETL_001",
                source="razorpay",
                reason="NO_EXACT_UTR",
                context={
                    "settlement_utr": "HDFCINBB2026800222",
                    "expected_net_paise": 97640,
                },
            )
        ]

        matches, stage3 = fuzzy_match_settlement_utr(
            unresolved,
            settlement_agg,
            bank_rows,
        )

        assert len(matches) == 0
        assert len(stage3) == 1

        handoff = stage3[0]

        assert handoff["status"] == "EXCEPTION"
        assert handoff["score"] >= 0.85
        assert handoff["amount_diff_paise"] == 12360
        assert handoff["date_diff_days"] == 9
        assert handoff["error_code"] == "AMOUNT_MISMATCH"

        assert handoff["failed_gates"] == [
            "AMOUNT_MISMATCH",
            "DATE_OFFSET_EXCEEDED",
        ]

    def test_fuzzy_utr_match_with_truncated_utr(self):
        # Stage 1 unresolved settlement with UTR "HDFCINBB2026800222"
        # Bank has narration containing "HDFCINBB202680022" (missing last digit)
        settlement_agg: List[Dict[str, Any]] = [
            {
                "settlement_id": "SETL_001",
                "settlement_utr": "HDFCINBB2026800222",
                "expected_net_paise": 97640,
                "settled_at": "2026-08-29T14:00:00",
            }
        ]
        bank_rows = [
            CanonicalBankRow(
                transaction_date="2026-08-29T00:00:00",
                value_date="2026-08-29T00:00:00",
                description="NEFT CR RAZORPAY SETTLEMENT HDFCINBB202680022",
                reference_number="",
                debit_paise=0,
                credit_paise=97640,
                balance_paise=100000000,
                currency="INR",
                utr="HDFCINBB202680022",
            )
        ]
        unresolved = [
            UnresolvedRecord(
                record_id="SETL_001",
                source="razorpay",
                reason="NO_EXACT_UTR",
                context={"settlement_utr": "HDFCINBB2026800222", "expected_net_paise": 97640},
            )
        ]

        matches, stage3 = fuzzy_match_settlement_utr(unresolved, settlement_agg, bank_rows)

        # Truncated UTR should still yield high score; amount/date OK → auto-match
        assert len(matches) == 1
        assert matches[0].settlement_id == "SETL_001"
        assert matches[0].match_method == "FUZZY_UTR"
        assert len(stage3) == 0

    def test_no_fuzzy_match_when_amount_differs_too_much(self):
        settlement_agg: List[Dict[str, Any]] = [
            {
                "settlement_id": "SETL_001",
                "settlement_utr": "HDFCINBB2026800222",
                "expected_net_paise": 97640,
                "settled_at": "2026-08-29T14:00:00",
            }
        ]
        bank_rows = [
            CanonicalBankRow(
                transaction_date="2026-08-29T00:00:00",
                value_date="2026-08-29T00:00:00",
                description="NEFT CR RAZORPAY SETTLEMENT HDFCINBB2026800222",
                reference_number="HDFCINBB2026800222",
                debit_paise=0,
                credit_paise=110000,  # large difference
                balance_paise=100000000,
                currency="INR",
                utr="HDFCINBB2026800222",
            )
        ]
        unresolved = [
            UnresolvedRecord(
                record_id="SETL_001",
                source="razorpay",
                reason="NO_EXACT_UTR",
                context={"settlement_utr": "HDFCINBB2026800222", "expected_net_paise": 97640},
            )
        ]

        matches, stage3 = fuzzy_match_settlement_utr(unresolved, settlement_agg, bank_rows)

        assert len(matches) == 0  # rejected due to amount difference
        # Goes to Stage 3 as EXCEPTION (amount mismatch)
        assert len(stage3) == 1
        assert stage3[0]["record"].record_id == "SETL_001"
        assert stage3[0]["status"] == "EXCEPTION"
        assert stage3[0]["error_code"] == "AMOUNT_MISMATCH"


class TestStage2EndToEnd:
    def test_reconcile_fuzzy_integration(self):
        merchant_rows = [
            CanonicalMerchantRow(
                merchant_order_id="INV-001",
                gateway_order_id="ORDER_ABC123X",
                gross_amount_paise=100000,
                order_created_at="2026-08-27T10:00:00",
            ),
            CanonicalMerchantRow(
                merchant_order_id="INV-002",
                gateway_order_id="ORDER_NO_MATCH",
                gross_amount_paise=50000,
                order_created_at="2026-08-27T11:00:00",
            ),
        ]

        razorpay_rows = [
            CanonicalRazorpayRow(
                transaction_entity="payment",
                entity_id="PAY_ABC",
                amount_paise=100000,
                currency="INR",
                order_id="ORDER_ABC123",
                settlement_id="SETL_001",
                payment_captured_at="2026-08-27T10:05:00",
            )
        ]

        bank_rows: list[CanonicalBankRow] = []

        unresolved = [
            UnresolvedRecord(
                record_id="INV-001",
                source="ledger",
                reason="NO_EXACT_ORDER_ID",
                context={"gateway_order_id": "ORDER_ABC123X"},
            ),
            UnresolvedRecord(
                record_id="INV-002",
                source="ledger",
                reason="NO_EXACT_ORDER_ID",
                context={"gateway_order_id": "ORDER_NO_MATCH"},
            ),
        ]

        fuzzy_ledger_matches, fuzzy_settlement_matches, stage3 = reconcile_fuzzy(
            merchant_rows,
            razorpay_rows,
            bank_rows,
            unresolved,
        )

        assert len(fuzzy_ledger_matches) == 1
        assert fuzzy_ledger_matches[0].gateway_order_id == "ORDER_ABC123X"
        assert fuzzy_ledger_matches[0].match_method == "FUZZY_ORDER_ID"

        assert len(fuzzy_settlement_matches) == 0

        assert len(stage3) == 1
        assert stage3[0]["record"].record_id == "INV-002"
        assert stage3[0]["status"] == "EXCEPTION"

class TestDateDurationBoundary:
    def test_exactly_three_days_is_inside_gate(self):
        assert _date_difference_days(
            "2026-08-20T09:00:00",
            "2026-08-23T09:00:00",
        ) == 3

    def test_three_days_twenty_three_hours_is_not_truncated_to_three(self):
        assert _date_difference_days(
            "2026-08-20T09:00:00",
            "2026-08-24T08:00:00",
        ) == 4

    def test_negative_three_days_twenty_three_hours_is_not_truncated(self):
        assert _date_difference_days(
            "2026-08-24T08:00:00",
            "2026-08-20T09:00:00",
        ) == -4


class TestDuplicateBankUTRHandling:
    def _make_settlement(self, settlement_id: str, utr: str, expected: int):
        return {
            "settlement_id": settlement_id,
            "settlement_utr": utr,
            "expected_net_paise": expected,
            "settled_at": "2026-09-01T09:00:00",
        }

    def _make_bank(self, utr: str, credit: int):
        return CanonicalBankRow(
            transaction_date="2026-09-01T00:00:00",
            value_date="2026-09-01T00:00:00",
            description=f"NEFT CR RAZORPAY SETTLEMENT {utr}",
            reference_number=utr,
            debit_paise=0,
            credit_paise=credit,
            balance_paise=100000000,
            currency="INR",
            utr=utr,
        )

    def _unresolved(self, settlement_id: str, utr: str, expected: int):
        return UnresolvedRecord(
            record_id=settlement_id,
            source="razorpay",
            reason="DUPLICATE_BANK_UTR",
            context={
                "settlement_utr": utr,
                "expected_net_paise": expected,
            },
        )

    def test_duplicate_utr_never_selects_unrelated_fuzzy_bank_row(self):
        duplicated_utr = "HDFCINBB202609DUP001"
        unrelated_utr = "HDFCINBB202609OTHER01"

        settlement = self._make_settlement(
            "SETL_DUP",
            duplicated_utr,
            120000,
        )
        bank_rows = [
            self._make_bank(duplicated_utr, 120000),
            self._make_bank(duplicated_utr, 120000),
            self._make_bank(unrelated_utr, 999999),
        ]
        unresolved = [
            self._unresolved("SETL_DUP", duplicated_utr, 120000)
        ]

        matches, stage3 = fuzzy_match_settlement_utr(
            unresolved,
            [settlement],
            bank_rows,
        )

        assert matches == []
        assert len(stage3) == 1
        handoff = stage3[0]
        assert handoff["status"] == "EXCEPTION"
        assert handoff["candidate_record_id"] is None
        assert handoff["review_evidence"]["candidate_record"] == {}
        assert "DUPLICATE_BANK_UTR" in handoff["reason"]

    def test_selected_bank_candidates_are_one_to_one(self):
        settlement_1 = self._make_settlement(
            "SETL_ONE",
            "HDFCINBB202609ONE001",
            100000,
        )
        settlement_2 = self._make_settlement(
            "SETL_TWO",
            "HDFCINBB202609TWO002",
            100000,
        )
        bank_rows = [
            self._make_bank("HDFCINBB202609ONE00", 100000),
            self._make_bank("HDFCINBB202609TWO00", 100000),
        ]
        unresolved = [
            self._unresolved(
                "SETL_ONE",
                "HDFCINBB202609ONE001",
                100000,
            ),
            self._unresolved(
                "SETL_TWO",
                "HDFCINBB202609TWO002",
                100000,
            ),
        ]

        matches, stage3 = fuzzy_match_settlement_utr(
            unresolved,
            [settlement_1, settlement_2],
            bank_rows,
        )

        selected = []
        selected.extend(
            item["candidate_record_id"]
            for item in stage3
            if item["candidate_record_id"] is not None
        )
        selected.extend(match.bank_reference for match in matches)

        assert len(selected) == len(set(selected))
