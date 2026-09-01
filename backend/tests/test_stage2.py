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
        assert handoff["date_diff_days"] == 8
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