from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from pydantic import ValidationError

from .schemas import Stage3ExceptionResult, Stage3LLMOutput
from .stage2_fuzzy_match import Stage3Handoff

load_dotenv()


DEFAULT_GEMINI_MODEL_CHAIN = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
)

GEMINI_MODEL_CHAIN = tuple(
    model.strip()
    for model in os.getenv(
        "GEMINI_MODEL_CHAIN",
        ",".join(DEFAULT_GEMINI_MODEL_CHAIN),
    ).split(",")
    if model.strip()
)

if not GEMINI_MODEL_CHAIN:
    raise RuntimeError("GEMINI_MODEL_CHAIN must contain at least one model.")


MAX_TRANSIENT_RETRIES_PER_MODEL = 2
BASE_BACKOFF_SECONDS = 1.0
PER_MODEL_RUN_BUDGET = 15


_NAME_LIKE_KEYS = {
    "customer_name",
    "payer_name",
    "beneficiary_name",
    "account_holder_name",
}


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True for SDK/API quota or HTTP 429-style errors."""
    message = str(exc).lower()
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)

    return (
        status_code == 429
        or "429" in message
        or "resource_exhausted" in message
        or "rate limit" in message
        or "quota" in message
    )


def mask_pii(
    context: Dict[str, Any],
    pii_map: Dict[str, str],
) -> Dict[str, Any]:
    """
    Preserve the existing basic name-like PII masking behavior.

    This helper remains for compatibility and separately masks known
    person-name values. Operational identifiers are tokenized by
    build_tokenized_grounding().
    """
    masked: Dict[str, Any] = {}

    for key, value in context.items():
        if (
            key in _NAME_LIKE_KEYS
            and isinstance(value, str)
            and value.strip()
        ):
            token = pii_map.get(value)

            if token is None:
                token = f"CUST_{len(pii_map) + 1:03d}"
                pii_map[value] = token

            masked[key] = token
        else:
            masked[key] = value

    return masked


def _build_settlement_lookup(
    razorpay_rows,
) -> Dict[str, Dict[str, Any]]:
    """
    Legacy compatibility helper.

    Stage 3 now consumes selected candidate evidence directly from Stage 2.
    This lookup remains only for legacy handoffs and old test compatibility.
    """
    result: Dict[str, Dict[str, Any]] = {}

    for row in razorpay_rows:
        settlement_id = row.settlement_id

        if not settlement_id:
            continue

        item = result.setdefault(
            settlement_id,
            {
                "settlement_id": settlement_id,
                "gross_sum_paise": 0,
                "fee_sum_paise": 0,
                "tax_sum_paise": 0,
                "settlement_utr": row.settlement_utr,
                "settled_at": row.settled_at,
            },
        )

        item["gross_sum_paise"] += row.amount_paise
        item["fee_sum_paise"] += row.fee_paise
        item["tax_sum_paise"] += row.tax_paise

        if not item["settlement_utr"]:
            item["settlement_utr"] = row.settlement_utr

        if not item["settled_at"]:
            item["settled_at"] = row.settled_at

    for item in result.values():
        item["expected_net_paise"] = (
            item["gross_sum_paise"]
            - item["fee_sum_paise"]
            - item["tax_sum_paise"]
        )

    return result


def _build_bank_lookup(bank_rows) -> Dict[str, Any]:
    """Legacy exact bank lookup retained for old handoffs/tests."""
    lookup: Dict[str, Any] = {}

    for row in bank_rows:
        for key in (row.utr, row.reference_number):
            if key:
                lookup[key] = row

    return lookup


def _build_merchant_lookup(merchant_rows) -> Dict[str, Any]:
    """Legacy merchant lookup retained for old handoffs/tests."""
    return {
        row.gateway_order_id: row
        for row in merchant_rows
        if row.gateway_order_id
    }


def _build_razorpay_order_lookup(razorpay_rows) -> Dict[str, Any]:
    """Legacy Razorpay order lookup retained for old handoffs/tests."""
    result: Dict[str, Any] = {}

    for row in razorpay_rows:
        if row.order_id and row.order_id not in result:
            result[row.order_id] = row

    return result


def build_grounding_record(
    handoff: Stage3Handoff,
    merchant_lookup: Dict[str, Any],
    razorpay_order_lookup: Dict[str, Any],
    settlement_lookup: Dict[str, Dict[str, Any]],
    bank_lookup: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build trusted backend-only grounding.

    Preferred behavior: use the exact selected candidate evidence captured by
    Stage 2 under handoff["review_evidence"].

    Fallback behavior: preserve the original lookup logic for old handoffs
    that do not contain review_evidence. Gemini never receives raw grounding.
    """
    review_evidence = handoff.get("review_evidence")

    if review_evidence:
        return {
            "record_type": review_evidence.get(
                "comparison_type",
                "unknown",
            ),
            "source_record": review_evidence.get("source_record", {}),
            "candidate_record": review_evidence.get(
                "candidate_record",
                {},
            ),
            "comparison": review_evidence.get("comparison", {}),
            "amount_diff_paise": handoff["amount_diff_paise"],
            "date_diff_days": handoff["date_diff_days"],
        }

    record = handoff["record"]

    if record.source == "ledger":
        gateway_order_id = record.context.get("gateway_order_id")
        merchant_row = merchant_lookup.get(gateway_order_id)
        razorpay_row = razorpay_order_lookup.get(gateway_order_id)

        return {
            "record_type": "ledger_order",
            "gateway_order_id": gateway_order_id,
            "merchant_amount_paise": (
                merchant_row.gross_amount_paise
                if merchant_row
                else None
            ),
            "razorpay_amount_paise": (
                razorpay_row.amount_paise
                if razorpay_row
                else None
            ),
            "razorpay_entity_found": razorpay_row is not None,
            "amount_diff_paise": handoff["amount_diff_paise"],
            "date_diff_days": handoff["date_diff_days"],
        }

    if record.source == "razorpay":
        settlement_id = record.record_id
        settlement = settlement_lookup.get(settlement_id, {})
        settlement_utr = settlement.get(
            "settlement_utr"
        ) or record.context.get("settlement_utr")
        bank_row = bank_lookup.get(settlement_utr) if settlement_utr else None

        return {
            "record_type": "settlement",
            "settlement_id": settlement_id,
            "settlement_utr": settlement_utr,
            "expected_net_paise": settlement.get(
                "expected_net_paise"
            ),
            "candidate_bank_credit_paise": (
                bank_row.credit_paise
                if bank_row
                else None
            ),
            "matching_bank_row_found": bank_row is not None,
            "amount_diff_paise": handoff["amount_diff_paise"],
            "date_diff_days": handoff["date_diff_days"],
        }

    return {
        "record_type": "unknown",
        "amount_diff_paise": handoff["amount_diff_paise"],
        "date_diff_days": handoff["date_diff_days"],
    }


def _is_identifier_key(key: str) -> bool:
    """
    Identify fields that contain operational identifiers and must be tokenized
    before anything is sent to Gemini.
    """
    normalized_key = key.lower()

    identifier_fragments = (
        "id",
        "utr",
        "reference",
        "record",
        "search",
    )

    return any(fragment in normalized_key for fragment in identifier_fragments)


def _token_prefix_for_field(field_name: str) -> str:
    """Create clear request-scoped token prefixes from evidence field names."""
    field_name = field_name.lower()

    if "merchant_order" in field_name:
        return "MERCHANT_ORDER"

    if "gateway_order" in field_name:
        return "GATEWAY_ORDER"

    if "razorpay_order" in field_name or field_name == "order_id":
        return "RAZORPAY_ORDER"

    if "entity" in field_name:
        return "RAZORPAY_ENTITY"

    if "settlement_utr" in field_name:
        return "SETTLEMENT_UTR"

    if "settlement_id" in field_name:
        return "SETTLEMENT"

    if "bank_reference" in field_name or "reference_number" in field_name:
        return "BANK_REFERENCE"

    if "bank_utr" in field_name:
        return "BANK_UTR"

    if "record_id" in field_name:
        return "RECORD"

    return "IDENTIFIER"


def _register_token(
    real_value: Any,
    role: str,
    field_name: str,
    source: str,
    real_to_token: Dict[str, str],
    token_to_real: Dict[str, str],
    token_metadata: Dict[str, Dict[str, str]],
) -> Optional[str]:
    """
    Create a request-scoped mapping for one real operational identifier.

    The maps are in-memory only:
    - real_to_token enables stable reuse during one classification;
    - token_to_real enables reviewer-only presentation restoration;
    - token_metadata enables existence, source, and role validation.
    """
    if real_value is None:
        return None

    normalized_value = str(real_value).strip()

    if not normalized_value:
        return None

    existing_token = real_to_token.get(normalized_value)

    if existing_token is not None:
        existing_metadata = token_metadata[existing_token]

        if (
            existing_metadata["role"] == "secondary"
            and role in {"source", "candidate"}
        ):
            existing_metadata["role"] = role

        return existing_token

    prefix = _token_prefix_for_field(field_name)
    token_number = 1 + sum(
        1
        for token in token_to_real
        if token.startswith(f"{prefix}_")
    )
    token = f"{prefix}_{token_number:03d}"

    real_to_token[normalized_value] = token
    token_to_real[token] = normalized_value
    token_metadata[token] = {
        "real_value": normalized_value,
        "role": role,
        "field": field_name,
        "source": source,
    }

    return token


def _tokenize_evidence_section(
    section: Dict[str, Any],
    role: str,
    source: str,
    real_to_token: Dict[str, str],
    token_to_real: Dict[str, str],
    token_metadata: Dict[str, Dict[str, str]],
) -> Dict[str, Any]:
    """
    Produce a masked copy of trusted evidence.

    Only identifier-like values are tokenized. Deterministic amounts, dates,
    similarities, and gate results remain available for evidence-grounded
    explanation. The original trusted evidence is never mutated.
    """
    masked: Dict[str, Any] = {}

    for key, value in section.items():
        if isinstance(value, dict):
            masked[key] = _tokenize_evidence_section(
                section=value,
                role=role,
                source=source,
                real_to_token=real_to_token,
                token_to_real=token_to_real,
                token_metadata=token_metadata,
            )
            continue

        if isinstance(value, list):
            masked[key] = value
            continue

        if _is_identifier_key(key):
            masked[key] = _register_token(
                real_value=value,
                role=role,
                field_name=key,
                source=source,
                real_to_token=real_to_token,
                token_to_real=token_to_real,
                token_metadata=token_metadata,
            )
        else:
            masked[key] = value

    return masked


def _first_token_for_role(
    token_metadata: Dict[str, Dict[str, str]],
    role: str,
) -> Optional[str]:
    """Return the first token registered under a requested role."""
    for token, metadata in token_metadata.items():
        if metadata["role"] == role:
            return token

    return None


def _legacy_review_evidence(
    handoff: Stage3Handoff,
    grounding: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Build minimal trusted evidence for legacy handoffs/tests.

    Real Stage 2 output after Milestone B contains richer review_evidence.
    This compatibility helper does not re-select a candidate.
    """
    record = handoff["record"]

    if record.source == "ledger":
        return {
            "exception_id": f"EXC-LEDGER-{record.record_id}",
            "comparison_type": "LEDGER_TO_RAZORPAY",
            "comparison_field": (
                "merchant.gateway_order_id ↔ razorpay.order_id"
            ),
            "source_record": {
                "record_id": record.record_id,
                "gateway_order_id": record.context.get(
                    "gateway_order_id"
                ),
                "merchant_amount_paise": (
                    grounding.get("merchant_amount_paise")
                    if grounding
                    else None
                ),
            },
            "candidate_record": {
                "razorpay_amount_paise": (
                    grounding.get("razorpay_amount_paise")
                    if grounding
                    else None
                ),
            },
            "comparison": {
                "similarity_score": handoff["score"],
                "amount_diff_paise": handoff["amount_diff_paise"],
                "date_diff_days": handoff["date_diff_days"],
                "failed_gates": handoff["failed_gates"],
            },
            "review_lookup": {
                "merchant_csv_search": record.context.get(
                    "gateway_order_id"
                ),
                "razorpay_csv_search": record.record_id,
                "bank_csv_search": None,
            },
        }

    return {
        "exception_id": f"EXC-SETTLEMENT-{record.record_id}",
        "comparison_type": "SETTLEMENT_TO_BANK",
        "comparison_field": "razorpay.settlement_utr ↔ bank.utr",
        "source_record": {
            "settlement_id": record.record_id,
            "settlement_utr": record.context.get("settlement_utr"),
            "expected_net_paise": (
                grounding.get("expected_net_paise")
                if grounding
                else None
            ),
        },
        "candidate_record": {
            "bank_reference_number": (
                grounding.get("settlement_utr")
                if grounding
                else None
            ),
            "bank_credit_paise": (
                grounding.get("candidate_bank_credit_paise")
                if grounding
                else None
            ),
        },
        "comparison": {
            "similarity_score": handoff["score"],
            "amount_diff_paise": handoff["amount_diff_paise"],
            "date_diff_days": handoff["date_diff_days"],
            "failed_gates": handoff["failed_gates"],
        },
        "review_lookup": {
            "merchant_csv_search": None,
            "razorpay_csv_search": record.record_id,
            "bank_csv_search": record.context.get("settlement_utr"),
        },
    }


def build_tokenized_grounding(
    handoff: Stage3Handoff,
    grounding: Optional[Dict[str, Any]] = None,
) -> Tuple[
    Dict[str, Any],
    Dict[str, str],
    Dict[str, str],
    Dict[str, Dict[str, str]],
]:
    """
    Build the masked evidence payload that Gemini is allowed to receive.

    Returns:
    - masked_evidence: safe payload with tokens instead of real identifiers;
    - real_to_token: request-scoped internal mapping;
    - token_to_real: request-scoped internal reverse mapping;
    - token_metadata: token role/field/source data for validation.

    No real operational ID is included in masked_evidence.
    """
    review_evidence = handoff.get("review_evidence")

    if not review_evidence:
        review_evidence = _legacy_review_evidence(
            handoff,
            grounding,
        )

    record = handoff["record"]
    source_record = review_evidence.get("source_record") or {}
    candidate_record = review_evidence.get("candidate_record") or {}
    review_lookup = review_evidence.get("review_lookup") or {}

    source_system = "merchant" if record.source == "ledger" else "razorpay"
    candidate_system = "razorpay" if record.source == "ledger" else "bank"

    real_to_token: Dict[str, str] = {}
    token_to_real: Dict[str, str] = {}
    token_metadata: Dict[str, Dict[str, str]] = {}

    masked_source_record = _tokenize_evidence_section(
        section=source_record,
        role="source",
        source=source_system,
        real_to_token=real_to_token,
        token_to_real=token_to_real,
        token_metadata=token_metadata,
    )

    masked_candidate_record = _tokenize_evidence_section(
        section=candidate_record,
        role="candidate",
        source=candidate_system,
        real_to_token=real_to_token,
        token_to_real=token_to_real,
        token_metadata=token_metadata,
    )

    masked_review_lookup = _tokenize_evidence_section(
        section=review_lookup,
        role="secondary",
        source=source_system,
        real_to_token=real_to_token,
        token_to_real=token_to_real,
        token_metadata=token_metadata,
    )

    source_record_token = _first_token_for_role(
        token_metadata,
        "source",
    )
    candidate_record_token = _first_token_for_role(
        token_metadata,
        "candidate",
    )

    workflow_mode = (
        "potential_match_recommendation"
        if handoff["status"] == "POTENTIAL_FUZZY_MATCH"
        else "exception_explanation"
    )

    return (
        {
            "workflow_mode": workflow_mode,
            "source_record": {
                "source_record_token": source_record_token,
                **masked_source_record,
            },
            "candidate_record": {
                "candidate_record_token": candidate_record_token,
                **masked_candidate_record,
            },
            "deterministic_evidence": {
                "stage2_status": handoff["status"],
                "similarity_score": handoff["score"],
                "amount_diff_paise": handoff["amount_diff_paise"],
                "date_diff_days": handoff["date_diff_days"],
                "error_code": handoff["error_code"],
                "failed_gates": handoff["failed_gates"],
                "comparison_type": review_evidence.get(
                    "comparison_type"
                ),
                "comparison_field": review_evidence.get(
                    "comparison_field"
                ),
            },
            "review_lookup_tokens": masked_review_lookup,
        },
        real_to_token,
        token_to_real,
        token_metadata,
    )


def restore_tokens_for_internal_reviewer(
    text: str,
    token_to_real: Dict[str, str],
) -> str:
    """
    Restore validated tokens only for internal reviewer-facing explanation text.

    Trusted review_evidence remains the authoritative ID source. Never pass
    this restored text back to Gemini.
    """
    restored = text

    for token in sorted(token_to_real, key=len, reverse=True):
        restored = restored.replace(token, token_to_real[token])

    return restored


_PROMPT_TEMPLATE = """You are a finance reconciliation assistant in a
human-in-the-loop workflow.

Use only the supplied masked evidence payload. Do not calculate, infer,
invent, or override identifiers, dates, amounts, fees, taxes, causes, or
financial outcomes.

Rules:
- Return only source/candidate tokens supplied in the payload.
- Never return raw IDs, UTRs, references, order IDs, or bank row indexes.
- Never return MATCHED or RESOLVED.
- STRONG_POTENTIAL_MATCH is legal only when workflow_mode is
  potential_match_recommendation and Stage 2 status is
  POTENTIAL_FUZZY_MATCH.
- STRONG_POTENTIAL_MATCH requires human_approval_required=true.
- For exception_explanation, return only EXCEPTION or NEEDS_MANUAL_REVIEW.
- Do not call a candidate strong if amount or date gates failed.
- If supplied evidence is insufficient, return NEEDS_MANUAL_REVIEW.
- Write 2-3 concise evidence-grounded sentences when possible.
- Do not provide chain-of-thought.

Masked evidence payload:
{masked_evidence}

Return only valid JSON:
{{
  "status": "STRONG_POTENTIAL_MATCH" | "EXCEPTION" | "NEEDS_MANUAL_REVIEW",
  "error_code": "MISSING_SETTLEMENT_UTR" | "DATE_OFFSET_EXCEEDED" |
                "FEE_MISMATCH" | "UNLINKED_ADJUSTMENT" |
                "AMOUNT_MISMATCH" | "OTHER" | null,
  "reasoning": "50 to 900 characters using only supplied evidence",
  "reported_amount_paise": "integer copied exactly from evidence or null",
  "source_record_token": "a supplied source token or null",
  "candidate_record_token": "a supplied candidate token or null",
  "human_approval_required": true | false
}}
"""


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    """Extract one JSON object from a Gemini response."""
    text = raw_text.strip()

    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            text,
        ).strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)

    if not match:
        raise ValueError("No JSON object found in LLM response.")

    parsed = json.loads(match.group(0))

    if not isinstance(parsed, dict):
        raise ValueError("LLM response JSON must be an object.")

    return parsed


def call_gemini_for_exception(
    stage2_evidence: Dict[str, Any],
    grounding: Optional[Dict[str, Any]],
    model_name: str,
) -> Dict[str, Any]:
    """
    Call Gemini with masked evidence only.

    The three-argument signature remains for compatibility with current tests.
    `grounding` is never included in Gemini's prompt.
    """
    del grounding

    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment.")

    client = genai.Client(api_key=api_key)

    prompt = _PROMPT_TEMPLATE.format(
        masked_evidence=json.dumps(stage2_evidence, indent=2),
    )

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config={
            "temperature": 0.0,
            "response_mime_type": "application/json",
        },
    )

    return _extract_json_object(response.text)


def _collect_integer_values(value: Any) -> set[int]:
    """Recursively collect trusted integers from nested evidence."""
    values: set[int] = set()

    if isinstance(value, dict):
        for nested_value in value.values():
            values.update(_collect_integer_values(nested_value))

    elif isinstance(value, list):
        for nested_value in value:
            values.update(_collect_integer_values(nested_value))

    elif isinstance(value, int) and not isinstance(value, bool):
        values.add(value)

    return values


def _cross_check_amount(
    llm_output: Stage3LLMOutput,
    trusted_evidence: Dict[str, Any],
) -> bool:
    """
    Confirm a Gemini-reported paise amount already exists in trusted evidence.

    `None` is allowed because an explanation may not need to name an amount.
    """
    if llm_output.reported_amount_paise is None:
        return True

    known_values = _collect_integer_values(trusted_evidence)

    return llm_output.reported_amount_paise in known_values


def _validate_returned_tokens(
    llm_output: Stage3LLMOutput,
    token_metadata: Dict[str, Dict[str, str]],
) -> bool:
    """
    Validate token existence and source/candidate roles.

    A source token must be supplied in `source_record_token`.
    A candidate token must be supplied in `candidate_record_token`.
    Null is allowed because an explanation may not reference an identifier.
    """
    if llm_output.source_record_token is not None:
        source_metadata = token_metadata.get(
            llm_output.source_record_token
        )

        if source_metadata is None:
            return False

        if source_metadata["role"] != "source":
            return False

    if llm_output.candidate_record_token is not None:
        candidate_metadata = token_metadata.get(
            llm_output.candidate_record_token
        )

        if candidate_metadata is None:
            return False

        if candidate_metadata["role"] != "candidate":
            return False

    return True


def _has_failed_financial_or_date_gate(
    handoff: Stage3Handoff,
) -> bool:
    """
    Return True when deterministic amount/date reconciliation gates failed.

    Gemini cannot make a strong candidate recommendation in this condition.
    """
    disqualifying_gates = {
        "AMOUNT_MISMATCH",
        "DATE_OFFSET_EXCEEDED",
    }

    return any(
        gate in disqualifying_gates
        for gate in handoff["failed_gates"]
    )


def _validate_status_gating(
    handoff: Stage3Handoff,
    llm_output: Stage3LLMOutput,
) -> bool:
    """
    Enforce the human-in-the-loop recommendation policy.

    A strong recommendation is valid only if:
    - Stage 2 marked it POTENTIAL_FUZZY_MATCH;
    - deterministic amount and date gates did not fail; and
    - Gemini explicitly requires human approval.

    Stage 2 exceptions cannot become strong recommendations.
    """
    if llm_output.status == "STRONG_POTENTIAL_MATCH":
        if handoff["status"] != "POTENTIAL_FUZZY_MATCH":
            return False

        if _has_failed_financial_or_date_gate(handoff):
            return False

        if not llm_output.human_approval_required:
            return False

        return True

    if handoff["status"] == "EXCEPTION":
        return llm_output.status in {
            "EXCEPTION",
            "NEEDS_MANUAL_REVIEW",
        }

    if handoff["status"] == "POTENTIAL_FUZZY_MATCH":
        return llm_output.status in {
            "STRONG_POTENTIAL_MATCH",
            "EXCEPTION",
            "NEEDS_MANUAL_REVIEW",
        }

    return False


def _fallback_result(
    handoff: Stage3Handoff,
    grounding: Dict[str, Any],
    fallback_reason: str,
    models_attempted: Optional[List[str]] = None,
) -> Stage3ExceptionResult:
    """
    Generate a deterministic safe fallback.

    An LLM/technical validation failure is never recast as a financial
    exception. Stage 2 evidence remains unchanged and reviewable.
    """
    record = handoff["record"]

    return Stage3ExceptionResult(
        record_id=record.record_id,
        source=record.source,
        stage2_status=handoff["status"],
        stage2_error_code=handoff["error_code"],
        stage2_failed_gates=handoff["failed_gates"],
        grounding=grounding,
        llm_status="NEEDS_MANUAL_REVIEW",
        llm_error_code="OTHER",
        llm_reasoning=(
            "Automated classification could not be completed safely. "
            "The deterministic Stage 2 evidence remains the source of truth."
        ),
        llm_reported_amount_paise=None,
        llm_model_used=None,
        models_attempted=models_attempted or [],
        ground_truth_amount_diff_paise=handoff["amount_diff_paise"],
        numeric_cross_check_passed=False,
        identifier_cross_check_passed=False,
        human_approval_required=False,
        source_record_id=handoff.get("source_record_id"),
        candidate_record_id=handoff.get("candidate_record_id"),
        review_evidence=handoff.get("review_evidence"),
        review_state="PENDING_REVIEW",
        used_fallback=True,
        fallback_reason=fallback_reason,
    )


def classify_exception(
    handoff: Stage3Handoff,
    grounding: Optional[Dict[str, Any]] = None,
    available_models: Optional[List[str]] = None,
    calls_used_by_model: Optional[Dict[str, int]] = None,
) -> Stage3ExceptionResult:
    """
    Classify one Stage 2 handoff using grounded, masked Gemini assistance.

    Stage 1 and Stage 2 remain the deterministic source of financial truth.
    A valid strong potential result remains PENDING_REVIEW until a human
    reviewer makes a separate frontend/session decision.
    """
    available_models = available_models or list(GEMINI_MODEL_CHAIN)

    if calls_used_by_model is None:
        calls_used_by_model = {
            model_name: 0
            for model_name in available_models
        }

    (
        masked_evidence,
        _real_to_token,
        token_to_real,
        token_metadata,
    ) = build_tokenized_grounding(
        handoff=handoff,
        grounding=grounding,
    )

    trusted_grounding = grounding or {
        "record_type": (
            handoff.get("review_evidence", {}).get(
                "comparison_type",
                "unknown",
            )
        ),
        "source_record": (
            handoff.get("review_evidence", {}).get(
                "source_record",
                {},
            )
        ),
        "candidate_record": (
            handoff.get("review_evidence", {}).get(
                "candidate_record",
                {},
            )
        ),
        "comparison": (
            handoff.get("review_evidence", {}).get(
                "comparison",
                {},
            )
        ),
        "amount_diff_paise": handoff["amount_diff_paise"],
        "date_diff_days": handoff["date_diff_days"],
    }

    models_attempted: List[str] = []
    failures: List[str] = []
    record = handoff["record"]

    for model_name in available_models:
        if calls_used_by_model.get(model_name, 0) >= PER_MODEL_RUN_BUDGET:
            failures.append(f"{model_name}: per-run budget reached")
            continue

        for attempt in range(1, MAX_TRANSIENT_RETRIES_PER_MODEL + 1):
            models_attempted.append(model_name)
            calls_used_by_model[model_name] = (
                calls_used_by_model.get(model_name, 0) + 1
            )

            try:
                raw_output = call_gemini_for_exception(
                    masked_evidence,
                    trusted_grounding,
                    model_name,
                )

                llm_output = Stage3LLMOutput.model_validate(raw_output)

                numeric_cross_check_passed = _cross_check_amount(
                    llm_output,
                    masked_evidence,
                )

                if not numeric_cross_check_passed:
                    raise ValueError(
                        "reported_amount_paise did not match a trusted "
                        "integer in the masked evidence payload"
                    )

                identifier_cross_check_passed = _validate_returned_tokens(
                    llm_output,
                    token_metadata,
                )

                if not identifier_cross_check_passed:
                    raise ValueError(
                        "source/candidate token was invented or had an "
                        "invalid token role"
                    )

                if not _validate_status_gating(handoff, llm_output):
                    raise ValueError(
                        "LLM status violates Stage 2 status, failed-gate, "
                        "or human-approval policy"
                    )

                restored_reasoning = restore_tokens_for_internal_reviewer(
                    llm_output.reasoning,
                    token_to_real,
                )

                return Stage3ExceptionResult(
                    record_id=record.record_id,
                    source=record.source,
                    stage2_status=handoff["status"],
                    stage2_error_code=handoff["error_code"],
                    stage2_failed_gates=handoff["failed_gates"],
                    grounding=trusted_grounding,
                    llm_status=llm_output.status,
                    llm_error_code=llm_output.error_code,
                    llm_reasoning=restored_reasoning,
                    llm_reported_amount_paise=(
                        llm_output.reported_amount_paise
                    ),
                    llm_model_used=model_name,
                    models_attempted=models_attempted,
                    ground_truth_amount_diff_paise=handoff[
                        "amount_diff_paise"
                    ],
                    numeric_cross_check_passed=True,
                    identifier_cross_check_passed=True,
                    human_approval_required=(
                        llm_output.human_approval_required
                    ),
                    source_record_id=handoff.get("source_record_id"),
                    candidate_record_id=handoff.get(
                        "candidate_record_id"
                    ),
                    review_evidence=handoff.get("review_evidence"),
                    review_state="PENDING_REVIEW",
                    used_fallback=False,
                    fallback_reason=None,
                )

            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                failures.append(f"{model_name}: invalid output: {exc}")
                break

            except Exception as exc:
                if _is_rate_limit_error(exc):
                    calls_used_by_model[model_name] = PER_MODEL_RUN_BUDGET
                    failures.append(
                        f"{model_name}: rate limited; switching model"
                    )
                    break

                failures.append(
                    f"{model_name}, attempt {attempt}: "
                    f"{type(exc).__name__}: {exc}"
                )

                if attempt < MAX_TRANSIENT_RETRIES_PER_MODEL:
                    time.sleep(
                        BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                    )
                else:
                    break

    return _fallback_result(
        handoff=handoff,
        grounding=trusted_grounding,
        fallback_reason=(
            "All configured Gemini models were unavailable or unsafe. "
            + " | ".join(failures)
        ),
        models_attempted=models_attempted,
    )


def reconcile_exceptions(
    stage3_handoffs: List[Stage3Handoff],
    merchant_rows,
    razorpay_rows,
    bank_rows,
) -> List[Stage3ExceptionResult]:
    """
    Process all Stage 2 handoffs through the guarded Stage 3 workflow.

    Normal Milestone B handoffs use trusted review_evidence directly. Legacy
    lookup support remains only for old inputs/tests without that evidence.
    """
    merchant_lookup = _build_merchant_lookup(merchant_rows)
    razorpay_order_lookup = _build_razorpay_order_lookup(razorpay_rows)
    settlement_lookup = _build_settlement_lookup(razorpay_rows)
    bank_lookup = _build_bank_lookup(bank_rows)

    calls_used_by_model = {
        model_name: 0
        for model_name in GEMINI_MODEL_CHAIN
    }

    results: List[Stage3ExceptionResult] = []

    for handoff in stage3_handoffs:
        trusted_grounding = build_grounding_record(
            handoff=handoff,
            merchant_lookup=merchant_lookup,
            razorpay_order_lookup=razorpay_order_lookup,
            settlement_lookup=settlement_lookup,
            bank_lookup=bank_lookup,
        )

        results.append(
            classify_exception(
                handoff=handoff,
                grounding=trusted_grounding,
                available_models=list(GEMINI_MODEL_CHAIN),
                calls_used_by_model=calls_used_by_model,
            )
        )

    return results