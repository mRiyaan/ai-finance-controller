import io
import pandas as pd
import pytest
from pydantic import ValidationError

from app.cleaners import (
    normalize_amount_to_paise,
    parse_datetime_to_iso,
    extract_utr_from_description,
    get_bank_reference_or_utr,
)
from app.schemas import (
    CanonicalMerchantRow,
    CanonicalRazorpayRow,
    CanonicalBankRow,
    DeadLetterRow,
)
from app.stage1_exact_match import reconcile


class TestPaiseConversion:
    def test_valid_rupee_amount_to_paise(self):
        assert normalize_amount_to_paise("1250.00") == 125000
        assert normalize_amount_to_paise("₹1,250.00") == 125000
        assert normalize_amount_to_paise("1,250") == 125000

    def test_invalid_amount_raises(self):
        with pytest.raises(ValueError):
            normalize_amount_to_paise("N/A")
        with pytest.raises(ValueError):
            normalize_amount_to_paise("")


class TestDateParsing:
    def test_valid_date_formats(self):
        iso1 = parse_datetime_to_iso("2026-08-27 10:15:00")
        assert iso1.startswith("2026-08-27T10:15:00")

        iso2 = parse_datetime_to_iso("2026-08-27")
        assert iso2.startswith("2026-08-27T00:00:00")

    def test_invalid_date_raises(self):
        with pytest.raises(ValueError):
            parse_datetime_to_iso("NOT-A-DATE")


class TestUTRExtraction:
    def test_extract_utr_from_narration(self):
        text = "NEFT-CR RZP SETL HDFCINBB2026800222"
        utr = extract_utr_from_description(text)
        assert utr == "HDFCINBB2026800222"

    def test_no_utr_in_text(self):
        text = "Just some random text without UTR"
        assert extract_utr_from_description(text) is None

    def test_get_bank_reference_or_utr_uses_reference_first(self):
        ref = get_bank_reference_or_utr("HDFCINBB2026800333", "NEFT-CR RZP SETL HDFCINBB2026800222")
        assert ref == "HDFCINBB2026800333"

    def test_get_bank_reference_or_utr_falls_back_to_narration(self):
        ref = get_bank_reference_or_utr("", "NEFT-CR RZP SETL HDFCINBB2026800222")
        assert ref == "HDFCINBB2026800222"


class TestStage1ExactMatching:
    def test_exact_order_id_match(self):
        merchant_csv = """merchant_order_id,gateway_order_id,gross_amount,order_created_at,customer_reference,order_status
INV-001,ORDER_ABC,1000.00,2026-08-27 10:00:00,CUST-1,PAID
"""
        razorpay_csv = """transaction_entity,entity_id,amount,currency,fee (exclusive tax),tax,order_id,settlement_id,settled_at,settlement_utr
payment,PAY_ABC,1000.00,INR,20.00,3.60,ORDER_ABC,SETL_001,2026-08-28 12:00:00,HDFCUTR001
"""
        bank_csv = """transaction_date,value_date,description,reference_number,debit,credit,balance,currency
2026-08-29,2026-08-29,NEFT CR RAZORPAY SETTLEMENT HDFCUTR001,HDFCUTR001,0.00,976.40,1000000.00,INR
"""

        merchant_df = pd.read_csv(io.StringIO(merchant_csv), dtype=str, keep_default_na=False)
        razorpay_df = pd.read_csv(io.StringIO(razorpay_csv), dtype=str, keep_default_na=False)
        bank_df = pd.read_csv(io.StringIO(bank_csv), dtype=str, keep_default_na=False)

        result = reconcile(merchant_df, razorpay_df, bank_df)

        assert len(result.matched_ledger_razorpay) == 1
        assert result.matched_ledger_razorpay[0].gateway_order_id == "ORDER_ABC"
        assert result.matched_ledger_razorpay[0].match_method == "EXACT_ORDER_ID"

    def test_amount_mismatch_flagged(self):
        merchant_csv = """merchant_order_id,gateway_order_id,gross_amount,order_created_at,customer_reference,order_status
INV-001,ORDER_ABC,1000.00,2026-08-27 10:00:00,CUST-1,PAID
"""
        razorpay_csv = """transaction_entity,entity_id,amount,currency,fee (exclusive tax),tax,order_id,settlement_id,settled_at,settlement_utr
payment,PAY_ABC,1050.00,INR,20.00,3.60,ORDER_ABC,SETL_001,2026-08-28 12:00:00,HDFCUTR001
"""
        bank_csv = """transaction_date,value_date,description,reference_number,debit,credit,balance,currency
2026-08-29,2026-08-29,NEFT CR RAZORPAY SETTLEMENT HDFCUTR001,HDFCUTR001,0.00,1026.40,1000000.00,INR
"""

        merchant_df = pd.read_csv(io.StringIO(merchant_csv), dtype=str, keep_default_na=False)
        razorpay_df = pd.read_csv(io.StringIO(razorpay_csv), dtype=str, keep_default_na=False)
        bank_df = pd.read_csv(io.StringIO(bank_csv), dtype=str, keep_default_na=False)

        result = reconcile(merchant_df, razorpay_df, bank_df)

        assert len(result.amount_mismatches) == 1
        assert result.amount_mismatches[0].gateway_order_id == "ORDER_ABC"
        assert result.amount_mismatches[0].merchant_amount_paise == 100000
        assert result.amount_mismatches[0].razorpay_amount_paise == 105000

    def test_unresolved_settlement_no_bank_row(self):
        merchant_csv = """merchant_order_id,gateway_order_id,gross_amount,order_created_at,customer_reference,order_status
INV-001,ORDER_ABC,1000.00,2026-08-27 10:00:00,CUST-1,PAID
"""
        razorpay_csv = """transaction_entity,entity_id,amount,currency,fee (exclusive tax),tax,order_id,settlement_id,settled_at,settlement_utr
payment,PAY_ABC,1000.00,INR,20.00,3.60,ORDER_ABC,SETL_001,2026-08-28 12:00:00,HDFCUTR001
"""
        bank_csv = """transaction_date,value_date,description,reference_number,debit,credit,balance,currency
2026-08-29,2026-08-29,NEFT CR RAZORPAY SETTLEMENT OTHERUTR,HDFCUTR999,0.00,5000.00,1000000.00,INR
"""

        merchant_df = pd.read_csv(io.StringIO(merchant_csv), dtype=str, keep_default_na=False)
        razorpay_df = pd.read_csv(io.StringIO(razorpay_csv), dtype=str, keep_default_na=False)
        bank_df = pd.read_csv(io.StringIO(bank_csv), dtype=str, keep_default_na=False)

        result = reconcile(merchant_df, razorpay_df, bank_df)

        assert len(result.unresolved_records) >= 1
        unresolved_settlements = [r for r in result.unresolved_records if r.source == "razorpay"]
        assert len(unresolved_settlements) >= 1
        assert any(r.record_id == "SETL_001" for r in unresolved_settlements)

    def test_dead_letters_for_invalid_rows(self):
        merchant_csv = """merchant_order_id,gateway_order_id,gross_amount,order_created_at,customer_reference,order_status
INV-001,ORDER_ABC,1000.00,2026-08-27 10:00:00,CUST-1,PAID
INV-002,ORDER_BAD,N/A,2026-08-27 10:00:00,CUST-2,PAID
"""
        razorpay_csv = """transaction_entity,entity_id,amount,currency,fee (exclusive tax),tax,order_id,settlement_id,settled_at,settlement_utr
payment,PAY_ABC,1000.00,INR,20.00,3.60,ORDER_ABC,SETL_001,2026-08-28 12:00:00,HDFCUTR001
payment,PAY_BAD,,INR,20.00,3.60,ORDER_BAD,SETL_001,2026-08-28 12:00:00,HDFCUTR001
"""
        bank_csv = """transaction_date,value_date,description,reference_number,debit,credit,balance,currency
2026-08-29,2026-08-29,NEFT CR RAZORPAY SETTLEMENT HDFCUTR001,HDFCUTR001,0.00,976.40,1000000.00,INR
2026-08-29,2026-08-29,NEFT CR RAZORPAY SETTLEMENT HDFCUTR002,HDFCUTR002,0.00,NOTANUMBER,1000000.00,INR
"""

        merchant_df = pd.read_csv(io.StringIO(merchant_csv), dtype=str, keep_default_na=False)
        razorpay_df = pd.read_csv(io.StringIO(razorpay_csv), dtype=str, keep_default_na=False)
        bank_df = pd.read_csv(io.StringIO(bank_csv), dtype=str, keep_default_na=False)

        result = reconcile(merchant_df, razorpay_df, bank_df)

        assert len(result.dead_letters) >= 2
        merchant_dead = [d for d in result.dead_letters if d.source == "merchant"]
        razorpay_dead = [d for d in result.dead_letters if d.source == "razorpay"]
        bank_dead = [d for d in result.dead_letters if d.source == "bank"]

        assert len(merchant_dead) >= 1
        assert "N/A" in merchant_dead[0].error_message

        assert len(razorpay_dead) >= 1

        assert len(bank_dead) >= 1