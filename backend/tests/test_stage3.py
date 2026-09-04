from copy import deepcopy
import json
import pytest
from pydantic import ValidationError

from app.schemas import UnresolvedRecord
from app import stage3_exception_reasoner as s3


VALID_EXCEPTION_REASONING = (
    "The supplied settlement and bank candidate have a verified amount "
    "difference and failed deterministic reconciliation gates, so this "
    "record remains an exception pending reviewer confirmation."
)

VALID_MANUAL_REVIEW_REASONING = (
    "The supplied evidence is insufficient to explain the reconciliation "
    "difference safely, so a reviewer must inspect the trusted source and "
    "candidate records before making any decision."
)

VALID_STRONG_MATCH_REASONING = (
    "The supplied source and candidate tokens refer to the selected fuzzy "
    "candidate and the deterministic amount and date gates passed. The "
    "similarity is below auto-match threshold, so human approval is required."
)

VALID_RETRY_REASONING = (
    "The retry returned a grounded response using only the supplied "
    "deterministic evidence, and the reported amount is present in the "
    "trusted source and candidate comparison data."
)

VALID_INVENTED_AMOUNT_REASONING = (
    "The response cites a reported amount that is not present in the "
    "trusted deterministic evidence, so the value must be rejected and "
    "sent to manual review."
)


def _make_settlement_handoff(
    *,
    status="EXCEPTION",
    failed_gates=None,
    amount_diff_paise=8490,
    date_diff_days=-4,
    score=0.8333333333,
):
    if failed_gates is None:
        failed_gates = [
            "AMOUNT_MISMATCH",
            "DATE_OFFSET_EXCEEDED",
            "BELOW_AUTO_MATCH_THRESHOLD",
        ]

    record = UnresolvedRecord(
        record_id="SETL_S005",
        source="razorpay",
        reason="NO_EXACT_SETTLEMENT_UTR",
        context={
            "settlement_utr": "HDFCINBB2026800444",
        },
    )

    return {
        "record": record,
        "status": status,
        "score": score,
        "amount_diff_paise": amount_diff_paise,
        "date_diff_days": date_diff_days,
        "error_code": (
            failed_gates[0]
            if failed_gates
            else None
        ),
        "failed_gates": failed_gates,
        "reason": "UTR candidate failed one or more Stage 2 reconciliation gates.",
        "source_record_id": "SETL_S005",
        "candidate_record_id": "HDFCINBB202680044",
        "review_evidence": {
            "exception_id": "EXC-SETTLEMENT-SETL_S005",
            "comparison_type": "SETTLEMENT_TO_BANK",
            "comparison_field": "razorpay.settlement_utr ↔ bank.utr",
            "source_record": {
                "settlement_id": "SETL_S005",
                "settlement_utr": "HDFCINBB2026800444",
                "expected_net_paise": 821638,
                "settled_at": "2026-08-24T13:00:00",
            },
            "candidate_record": {
                "bank_row_index": 4,
                "bank_reference_number": "HDFCINBB202680044",
                "bank_utr": "HDFCINBB202680044",
                "bank_credit_paise": 830128,
                "bank_transaction_date": "2026-08-20T00:00:00",
                "bank_value_date": "2026-08-20T00:00:00",
            },
            "comparison": {
                "similarity_score": score,
                "amount_diff_paise": amount_diff_paise,
                "date_diff_days": date_diff_days,
                "failed_gates": failed_gates,
            },
            "review_lookup": {
                "merchant_csv_search": None,
                "razorpay_csv_search": "SETL_S005",
                "bank_csv_search": "HDFCINBB202680044",
            },
        },
    }


def _make_potential_settlement_handoff():
    return _make_settlement_handoff(
        status="POTENTIAL_FUZZY_MATCH",
        failed_gates=["BELOW_AUTO_MATCH_THRESHOLD"],
        amount_diff_paise=0,
        date_diff_days=1,
        score=0.80,
    )


def _make_ledger_handoff():
    record = UnresolvedRecord(
        record_id="INV-2026-1010",
        source="ledger",
        reason="NO_EXACT_ORDER_ID",
        context={
            "gateway_order_id": "ORDER_Q1010XZ",
        },
    )

    return {
        "record": record,
        "status": "EXCEPTION",
        "score": 0.9230769231,
        "amount_diff_paise": 16445,
        "date_diff_days": -2,
        "error_code": "AMOUNT_MISMATCH",
        "failed_gates": ["AMOUNT_MISMATCH"],
        "reason": "Order-ID candidate failed one or more Stage 2 reconciliation gates.",
        "source_record_id": "INV-2026-1010",
        "candidate_record_id": "PAY_Q1011AB",
        "review_evidence": {
            "exception_id": "EXC-LEDGER-INV-2026-1010",
            "comparison_type": "LEDGER_TO_RAZORPAY",
            "comparison_field": "merchant.gateway_order_id ↔ razorpay.order_id",
            "source_record": {
                "merchant_order_id": "INV-2026-1010",
                "gateway_order_id": "ORDER_Q1010XZ",
                "gross_amount_paise": 976400,
                "order_created_at": "2026-08-24T10:00:00",
            },
            "candidate_record": {
                "razorpay_entity_id": "PAY_Q1011AB",
                "razorpay_order_id": "ORDER_Q1010XY",
                "razorpay_amount_paise": 992845,
                "payment_captured_at": "2026-08-22T10:00:00",
                "settlement_id": "SETL_S006",
                "settlement_utr": "HDFCINBB2026800455",
            },
            "comparison": {
                "similarity_score": 0.9230769231,
                "amount_diff_paise": 16445,
                "date_diff_days": -2,
                "failed_gates": ["AMOUNT_MISMATCH"],
            },
            "review_lookup": {
                "merchant_csv_search": "INV-2026-1010",
                "razorpay_csv_search": "PAY_Q1011AB",
                "bank_csv_search": None,
            },
        },
    }


def _legacy_grounding_for(handoff):
    review_evidence = handoff["review_evidence"]

    return {
        "record_type": review_evidence["comparison_type"],
        "source_record": review_evidence["source_record"],
        "candidate_record": review_evidence["candidate_record"],
        "comparison": review_evidence["comparison"],
        "amount_diff_paise": handoff["amount_diff_paise"],
        "date_diff_days": handoff["date_diff_days"],
    }


def _tokens_for(handoff, grounding=None):
    (
        masked_evidence,
        real_to_token,
        token_to_real,
        token_metadata,
    ) = s3.build_tokenized_grounding(
        handoff=handoff,
        grounding=grounding,
    )

    source_token = masked_evidence["source_record"]["source_record_token"]
    candidate_token = masked_evidence["candidate_record"][
        "candidate_record_token"
    ]

    return (
        masked_evidence,
        real_to_token,
        token_to_real,
        token_metadata,
        source_token,
        candidate_token,
    )


class TestMaskPii:
    def test_masks_known_name_fields(self):
        pii_map = {}
        context = {
            "customer_name": "Rahul Sharma",
            "settlement_id": "SETL_S005",
        }

        masked = s3.mask_pii(context, pii_map)

        assert masked["customer_name"] == "CUST_001"
        assert masked["settlement_id"] == "SETL_S005"
        assert pii_map == {"Rahul Sharma": "CUST_001"}

    def test_reuses_same_token_for_repeated_name(self):
        pii_map = {}

        first = s3.mask_pii(
            {"customer_name": "Rahul Sharma"},
            pii_map,
        )
        second = s3.mask_pii(
            {"payer_name": "Rahul Sharma"},
            pii_map,
        )

        assert first["customer_name"] == "CUST_001"
        assert second["payer_name"] == "CUST_001"
        assert pii_map == {"Rahul Sharma": "CUST_001"}


class TestStage3LLMOutputSchema:
    def test_rejects_reasoning_shorter_than_50_characters(self):
        with pytest.raises(ValidationError):
            s3.Stage3LLMOutput(
                status="EXCEPTION",
                error_code="OTHER",
                reasoning="Too short.",
                reported_amount_paise=None,
            )

    def test_rejects_reasoning_longer_than_900_characters(self):
        with pytest.raises(ValidationError):
            s3.Stage3LLMOutput(
                status="EXCEPTION",
                error_code="OTHER",
                reasoning="A" * 901,
                reported_amount_paise=None,
            )

    def test_rejects_old_resolved_status(self):
        with pytest.raises(ValidationError):
            s3.Stage3LLMOutput(
                status="RESOLVED",
                error_code=None,
                reasoning=VALID_EXCEPTION_REASONING,
                reported_amount_paise=None,
            )

    def test_rejects_old_matched_status(self):
        with pytest.raises(ValidationError):
            s3.Stage3LLMOutput(
                status="MATCHED",
                error_code=None,
                reasoning=VALID_EXCEPTION_REASONING,
                reported_amount_paise=None,
            )

    def test_accepts_new_valid_statuses(self):
        for status in (
            "STRONG_POTENTIAL_MATCH",
            "EXCEPTION",
            "NEEDS_MANUAL_REVIEW",
        ):
            result = s3.Stage3LLMOutput(
                status=status,
                error_code="OTHER",
                reasoning=VALID_EXCEPTION_REASONING,
                reported_amount_paise=None,
            )

            assert result.status == status


class TestCrossCheckAmount:
    def test_passes_when_amount_matches_grounding(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
        )

        assert s3._cross_check_amount(llm_output, grounding) is True

    def test_fails_when_amount_is_invented(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=999999,
        )

        assert s3._cross_check_amount(llm_output, grounding) is False

    def test_passes_when_no_amount_reported(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        llm_output = s3.Stage3LLMOutput(
            status="NEEDS_MANUAL_REVIEW",
            error_code="OTHER",
            reasoning=VALID_MANUAL_REVIEW_REASONING,
            reported_amount_paise=None,
        )

        assert s3._cross_check_amount(llm_output, grounding) is True


class TestTokenizedGrounding:
    def test_masks_real_ids_before_gemini_payload(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            masked_evidence,
            real_to_token,
            token_to_real,
            token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        serialized_payload = str(masked_evidence)

        assert "SETL_S005" not in serialized_payload
        assert "HDFCINBB2026800444" not in serialized_payload
        assert "HDFCINBB202680044" not in serialized_payload

        assert source_token is not None
        assert candidate_token is not None
        assert source_token in token_metadata
        assert candidate_token in token_metadata

        assert token_metadata[source_token]["role"] == "source"
        assert token_metadata[candidate_token]["role"] == "candidate"

        assert real_to_token["SETL_S005"] == source_token
        assert token_to_real[source_token] == "SETL_S005"

    def test_preserves_real_ids_in_review_evidence(self):
        handoff = _make_settlement_handoff()

        review_evidence = handoff["review_evidence"]

        assert review_evidence["source_record"]["settlement_id"] == "SETL_S005"
        assert (
            review_evidence["candidate_record"]["bank_reference_number"]
            == "HDFCINBB202680044"
        )
        assert review_evidence["candidate_record"]["bank_row_index"] == 4

    def test_uses_potential_match_workflow_only_for_potential_status(self):
        potential_handoff = _make_potential_settlement_handoff()
        exception_handoff = _make_settlement_handoff()

        potential_payload, *_ = _tokens_for(potential_handoff)
        exception_payload, *_ = _tokens_for(exception_handoff)

        assert (
            potential_payload["workflow_mode"]
            == "potential_match_recommendation"
        )
        assert (
            exception_payload["workflow_mode"]
            == "exception_explanation"
        )

    def test_restores_tokens_only_for_internal_reviewer_text(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            token_to_real,
            _token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        text = (
            f"{source_token} differs from {candidate_token} according to "
            "the deterministic amount and date evidence."
        )

        restored = s3.restore_tokens_for_internal_reviewer(
            text,
            token_to_real,
        )

        assert "SETL_S005" in restored
        assert "HDFCINBB202680044" in restored
        assert source_token not in restored
        assert candidate_token not in restored


class TestTokenValidation:
    def test_accepts_valid_source_and_candidate_tokens(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
            source_record_token=source_token,
            candidate_record_token=candidate_token,
        )

        assert s3._validate_returned_tokens(
            llm_output,
            token_metadata,
        ) is True

    def test_rejects_invented_source_token(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            token_metadata,
            _source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
            source_record_token="SETTLEMENT_999",
            candidate_record_token=candidate_token,
        )

        assert s3._validate_returned_tokens(
            llm_output,
            token_metadata,
        ) is False

    def test_rejects_invented_candidate_token(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            token_metadata,
            source_token,
            _candidate_token,
        ) = _tokens_for(handoff, grounding)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
            source_record_token=source_token,
            candidate_record_token="BANK_REFERENCE_999",
        )

        assert s3._validate_returned_tokens(
            llm_output,
            token_metadata,
        ) is False

    def test_rejects_wrong_token_role(self):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
            source_record_token=candidate_token,
            candidate_record_token=source_token,
        )

        assert s3._validate_returned_tokens(
            llm_output,
            token_metadata,
        ) is False


class TestStatusGating:
    def test_allows_strong_potential_match_for_valid_potential_handoff(self):
        handoff = _make_potential_settlement_handoff()

        llm_output = s3.Stage3LLMOutput(
            status="STRONG_POTENTIAL_MATCH",
            error_code=None,
            reasoning=VALID_STRONG_MATCH_REASONING,
            reported_amount_paise=0,
            human_approval_required=True,
        )

        assert s3._validate_status_gating(handoff, llm_output) is True

    def test_rejects_strong_potential_match_for_exception_handoff(self):
        handoff = _make_settlement_handoff()

        llm_output = s3.Stage3LLMOutput(
            status="STRONG_POTENTIAL_MATCH",
            error_code=None,
            reasoning=VALID_STRONG_MATCH_REASONING,
            reported_amount_paise=830128,
            human_approval_required=True,
        )

        assert s3._validate_status_gating(handoff, llm_output) is False

    def test_rejects_strong_potential_without_human_approval(self):
        handoff = _make_potential_settlement_handoff()

        llm_output = s3.Stage3LLMOutput(
            status="STRONG_POTENTIAL_MATCH",
            error_code=None,
            reasoning=VALID_STRONG_MATCH_REASONING,
            reported_amount_paise=0,
            human_approval_required=False,
        )

        assert s3._validate_status_gating(handoff, llm_output) is False

    def test_rejects_strong_potential_when_amount_gate_failed(self):
        handoff = _make_settlement_handoff(
            status="POTENTIAL_FUZZY_MATCH",
            failed_gates=[
                "AMOUNT_MISMATCH",
                "BELOW_AUTO_MATCH_THRESHOLD",
            ],
            amount_diff_paise=8490,
            date_diff_days=1,
            score=0.80,
        )

        llm_output = s3.Stage3LLMOutput(
            status="STRONG_POTENTIAL_MATCH",
            error_code=None,
            reasoning=VALID_STRONG_MATCH_REASONING,
            reported_amount_paise=830128,
            human_approval_required=True,
        )

        assert s3._validate_status_gating(handoff, llm_output) is False

    def test_rejects_strong_potential_when_date_gate_failed(self):
        handoff = _make_settlement_handoff(
            status="POTENTIAL_FUZZY_MATCH",
            failed_gates=[
                "DATE_OFFSET_EXCEEDED",
                "BELOW_AUTO_MATCH_THRESHOLD",
            ],
            amount_diff_paise=0,
            date_diff_days=-4,
            score=0.80,
        )

        llm_output = s3.Stage3LLMOutput(
            status="STRONG_POTENTIAL_MATCH",
            error_code=None,
            reasoning=VALID_STRONG_MATCH_REASONING,
            reported_amount_paise=0,
            human_approval_required=True,
        )

        assert s3._validate_status_gating(handoff, llm_output) is False

    def test_allows_exception_result_for_exception_handoff(self):
        handoff = _make_settlement_handoff()

        llm_output = s3.Stage3LLMOutput(
            status="EXCEPTION",
            error_code="AMOUNT_MISMATCH",
            reasoning=VALID_EXCEPTION_REASONING,
            reported_amount_paise=830128,
        )

        assert s3._validate_status_gating(handoff, llm_output) is True

    def test_allows_manual_review_for_exception_handoff(self):
        handoff = _make_settlement_handoff()

        llm_output = s3.Stage3LLMOutput(
            status="NEEDS_MANUAL_REVIEW",
            error_code="OTHER",
            reasoning=VALID_MANUAL_REVIEW_REASONING,
            reported_amount_paise=None,
        )

        assert s3._validate_status_gating(handoff, llm_output) is True


class TestClassifyExceptionRetryAndFallback:
    def test_succeeds_on_first_valid_response(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            _token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        def fake_call(stage2_evidence, grounding_arg, model_name):
            assert "SETL_S005" not in str(stage2_evidence)
            assert "HDFCINBB202680044" not in str(stage2_evidence)

            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": (
                    f"{source_token} differs from {candidate_token} by the "
                    "supplied deterministic amount evidence, and the "
                    "failed reconciliation gates require exception review."
                ),
                "reported_amount_paise": 830128,
                "source_record_token": source_token,
                "candidate_record_token": candidate_token,
                "human_approval_required": False,
            }

        monkeypatch.setattr(s3, "call_gemini_for_exception", fake_call)

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is False
        assert result.llm_status == "EXCEPTION"
        assert result.llm_error_code == "AMOUNT_MISMATCH"
        assert result.llm_reported_amount_paise == 830128
        assert result.numeric_cross_check_passed is True
        assert result.identifier_cross_check_passed is True
        assert result.llm_model_used == "gemini-3.8-flash"
        assert result.models_attempted == ["gemini-3.8-flash"]

        assert "SETL_S005" in result.llm_reasoning
        assert "HDFCINBB202680044" in result.llm_reasoning

        assert result.source_record_id == "SETL_S005"
        assert result.candidate_record_id == "HDFCINBB202680044"
        assert result.review_evidence is not None
        assert result.review_evidence.model_dump() == handoff["review_evidence"]
        assert result.review_state == "PENDING_REVIEW"

    def test_retries_then_succeeds(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)
        calls = {"count": 0}

        def flaky_call(stage2_evidence, grounding_arg, model_name):
            calls["count"] += 1

            if calls["count"] < 2:
                raise RuntimeError("simulated transient API error")

            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": VALID_RETRY_REASONING,
                "reported_amount_paise": 821638,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(s3, "call_gemini_for_exception", flaky_call)
        monkeypatch.setattr(s3.time, "sleep", lambda seconds: None)

        result = s3.classify_exception(handoff, grounding)

        assert calls["count"] == 2
        assert result.used_fallback is False
        assert result.llm_status == "EXCEPTION"
        assert result.llm_model_used == "gemini-3.8-flash"
        assert result.models_attempted == [
            "gemini-3.8-flash",
            "gemini-3.8-flash",
        ]

    def test_falls_back_after_invalid_json(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def invalid_json_call(stage2_evidence, grounding_arg, model_name):
            raise json.JSONDecodeError(
                "invalid JSON",
                "not-json",
                0,
            )

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            invalid_json_call,
        )
        monkeypatch.setattr(s3.time, "sleep", lambda seconds: None)

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert result.numeric_cross_check_passed is False
        assert result.identifier_cross_check_passed is False
        assert result.llm_model_used is None
        assert "invalid output" in result.fallback_reason

    def test_falls_back_when_amount_is_invented(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def inventive_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": VALID_INVENTED_AMOUNT_REASONING,
                "reported_amount_paise": 123456789,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(s3, "call_gemini_for_exception", inventive_call)
        monkeypatch.setattr(s3.time, "sleep", lambda seconds: None)

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert "reported_amount_paise" in result.fallback_reason

    def test_falls_back_when_source_token_is_invented(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def invented_token_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": VALID_EXCEPTION_REASONING,
                "reported_amount_paise": 830128,
                "source_record_token": "SETTLEMENT_999",
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            invented_token_call,
        )

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert "token" in result.fallback_reason.lower()

    def test_falls_back_when_token_role_is_wrong(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            _token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        def wrong_role_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": VALID_EXCEPTION_REASONING,
                "reported_amount_paise": 830128,
                "source_record_token": candidate_token,
                "candidate_record_token": source_token,
                "human_approval_required": False,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            wrong_role_call,
        )

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert "invalid token role" in result.fallback_reason.lower()

    def test_never_raises_on_persistent_failure(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def always_fail(stage2_evidence, grounding_arg, model_name):
            raise RuntimeError("simulated persistent API failure")

        monkeypatch.setattr(s3, "call_gemini_for_exception", always_fail)
        monkeypatch.setattr(s3.time, "sleep", lambda seconds: None)

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert result.llm_model_used is None
        assert result.numeric_cross_check_passed is False
        assert result.identifier_cross_check_passed is False
        assert len(result.models_attempted) == 6


class TestStrongPotentialMatchWorkflow:
    def test_returns_valid_strong_potential_match(self, monkeypatch):
        handoff = _make_potential_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        (
            _masked_evidence,
            _real_to_token,
            _token_to_real,
            _token_metadata,
            source_token,
            candidate_token,
        ) = _tokens_for(handoff, grounding)

        def strong_match_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "STRONG_POTENTIAL_MATCH",
                "error_code": None,
                "reasoning": (
                    f"{source_token} and {candidate_token} satisfy the "
                    "supplied amount and date gates, while similarity is "
                    "below auto-match threshold. Human approval is required."
                ),
                "reported_amount_paise": 0,
                "source_record_token": source_token,
                "candidate_record_token": candidate_token,
                "human_approval_required": True,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            strong_match_call,
        )

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is False
        assert result.llm_status == "STRONG_POTENTIAL_MATCH"
        assert result.human_approval_required is True
        assert result.review_state == "PENDING_REVIEW"
        assert result.numeric_cross_check_passed is True
        assert result.identifier_cross_check_passed is True

    def test_falls_back_when_exception_becomes_strong_match(
        self,
        monkeypatch,
    ):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def invalid_strong_match_call(
            stage2_evidence,
            grounding_arg,
            model_name,
        ):
            return {
                "status": "STRONG_POTENTIAL_MATCH",
                "error_code": None,
                "reasoning": VALID_STRONG_MATCH_REASONING,
                "reported_amount_paise": 830128,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": True,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            invalid_strong_match_call,
        )

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert "status violates" in result.fallback_reason.lower()

    def test_falls_back_when_strong_match_lacks_human_approval(
        self,
        monkeypatch,
    ):
        handoff = _make_potential_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def no_approval_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "STRONG_POTENTIAL_MATCH",
                "error_code": None,
                "reasoning": VALID_STRONG_MATCH_REASONING,
                "reported_amount_paise": 0,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            no_approval_call,
        )

        result = s3.classify_exception(handoff, grounding)

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert "human-approval policy" in result.fallback_reason.lower()


class TestBuildGroundingRecord:
    def test_uses_stage2_review_evidence_without_exact_lookup(self):
        handoff = _make_settlement_handoff()

        grounding = s3.build_grounding_record(
            handoff=handoff,
            merchant_lookup={},
            razorpay_order_lookup={},
            settlement_lookup={},
            bank_lookup={},
        )

        assert grounding["record_type"] == "SETTLEMENT_TO_BANK"
        assert (
            grounding["source_record"]["settlement_id"]
            == "SETL_S005"
        )
        assert (
            grounding["candidate_record"]["bank_reference_number"]
            == "HDFCINBB202680044"
        )
        assert grounding["candidate_record"]["bank_row_index"] == 4
        assert (
            grounding["candidate_record"]["bank_credit_paise"]
            == 830128
        )

    def test_ledger_grounding_uses_stage2_selected_candidate(self):
        handoff = _make_ledger_handoff()

        grounding = s3.build_grounding_record(
            handoff=handoff,
            merchant_lookup={},
            razorpay_order_lookup={},
            settlement_lookup={},
            bank_lookup={},
        )

        assert grounding["record_type"] == "LEDGER_TO_RAZORPAY"
        assert (
            grounding["source_record"]["merchant_order_id"]
            == "INV-2026-1010"
        )
        assert (
            grounding["candidate_record"]["razorpay_entity_id"]
            == "PAY_Q1011AB"
        )
        assert (
            grounding["candidate_record"]["razorpay_order_id"]
            == "ORDER_Q1010XY"
        )


class TestReconcileExceptionsOrchestrator:
    def test_runs_all_handoffs_and_never_mutates_inputs(self, monkeypatch):
        settlement_handoff = _make_settlement_handoff()
        ledger_handoff = _make_ledger_handoff()

        handoffs = [
            settlement_handoff,
            ledger_handoff,
        ]

        original_handoffs = deepcopy(handoffs)

        def fake_call(stage2_evidence, grounding_arg, model_name):
            return {
                "status": "EXCEPTION",
                "error_code": "OTHER",
                "reasoning": VALID_EXCEPTION_REASONING,
                "reported_amount_paise": None,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(s3, "call_gemini_for_exception", fake_call)

        results = s3.reconcile_exceptions(
            stage3_handoffs=handoffs,
            merchant_rows=[],
            razorpay_rows=[],
            bank_rows=[],
        )

        assert len(results) == 2
        assert all(result.used_fallback is False for result in results)
        assert all(
            result.llm_status == "EXCEPTION"
            for result in results
        )
        assert handoffs == original_handoffs


class TestModelFailover:
    def test_switches_to_next_model_after_rate_limit(self, monkeypatch):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)
        models_called = []

        def quota_then_success(stage2_evidence, grounding_arg, model_name):
            models_called.append(model_name)

            if model_name == "gemini-3.8-flash":
                raise RuntimeError(
                    "429 RESOURCE_EXHAUSTED: quota exceeded"
                )

            return {
                "status": "EXCEPTION",
                "error_code": "AMOUNT_MISMATCH",
                "reasoning": VALID_EXCEPTION_REASONING,
                "reported_amount_paise": 830128,
                "source_record_token": None,
                "candidate_record_token": None,
                "human_approval_required": False,
            }

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            quota_then_success,
        )
        monkeypatch.setattr(
            s3,
            "GEMINI_MODEL_CHAIN",
            (
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
            ),
        )
        monkeypatch.setattr(s3, "PER_MODEL_RUN_BUDGET", 15)

        calls_used_by_model = {
            "gemini-3.8-flash": 0,
            "gemini-3.7-flash": 0,
            "gemini-3.6-flash": 0,
        }

        result = s3.classify_exception(
            handoff=handoff,
            grounding=grounding,
            available_models=list(s3.GEMINI_MODEL_CHAIN),
            calls_used_by_model=calls_used_by_model,
        )

        assert result.used_fallback is False
        assert result.llm_status == "EXCEPTION"
        assert result.llm_model_used == "gemini-3.7-flash"
        assert result.models_attempted == [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
        ]
        assert models_called == [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
        ]
        assert calls_used_by_model["gemini-3.8-flash"] == 15
        assert calls_used_by_model["gemini-3.7-flash"] == 1

    def test_falls_back_after_all_models_are_rate_limited(
        self,
        monkeypatch,
    ):
        handoff = _make_settlement_handoff()
        grounding = _legacy_grounding_for(handoff)

        def always_rate_limited(stage2_evidence, grounding_arg, model_name):
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED: quota exceeded"
            )

        monkeypatch.setattr(
            s3,
            "call_gemini_for_exception",
            always_rate_limited,
        )
        monkeypatch.setattr(
            s3,
            "GEMINI_MODEL_CHAIN",
            (
                "gemini-3.8-flash",
                "gemini-3.7-flash",
                "gemini-3.6-flash",
            ),
        )
        monkeypatch.setattr(s3, "PER_MODEL_RUN_BUDGET", 15)

        calls_used_by_model = {
            "gemini-3.8-flash": 0,
            "gemini-3.7-flash": 0,
            "gemini-3.6-flash": 0,
        }

        result = s3.classify_exception(
            handoff=handoff,
            grounding=grounding,
            available_models=list(s3.GEMINI_MODEL_CHAIN),
            calls_used_by_model=calls_used_by_model,
        )

        assert result.used_fallback is True
        assert result.llm_status == "NEEDS_MANUAL_REVIEW"
        assert result.llm_model_used is None
        assert result.models_attempted == [
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-3.6-flash",
        ]
        assert calls_used_by_model == {
            "gemini-3.8-flash": 15,
            "gemini-3.7-flash": 15,
            "gemini-3.6-flash": 15,
        }