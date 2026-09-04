from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from .cleaners import (
    normalize_identifier,
    parse_datetime_to_iso,
)


# Raw input models
# These models represent values read directly from CSV files.
# Raw amount fields remain strings because CSV values can contain:
# "1,250.00", "₹1,250.00", "", etc.
#
# Conversion from raw rupees to integer paise belongs in the
# preprocessing / CSV ingestion layer, not in canonical models.


class RawMerchantRow(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: Optional[str] = None
    gross_amount: Optional[str] = None
    order_created_at: Optional[str] = None
    customer_reference: Optional[str] = None
    order_status: Optional[str] = None
    source_system: Optional[str] = None


class RawRazorpayRow(BaseModel):
    transaction_entity: Optional[str] = None
    entity_id: Optional[str] = None
    amount: Optional[str] = None
    currency: Optional[str] = None
    fee_exclusive_tax: Optional[str] = None
    tax: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    payment_method: Optional[str] = None
    entity_created_at: Optional[str] = None
    payment_captured_at: Optional[str] = None
    order_id: Optional[str] = None
    settlement_id: Optional[str] = None
    settled_at: Optional[str] = None
    settlement_utr: Optional[str] = None
    settled_by: Optional[str] = None


class RawBankRow(BaseModel):
    transaction_date: Optional[str] = None
    value_date: Optional[str] = None
    description: Optional[str] = None
    reference_number: Optional[str] = None
    debit: Optional[str] = None
    credit: Optional[str] = None
    balance: Optional[str] = None
    currency: Optional[str] = None


# Canonical internal models
# IMPORTANT CONTRACT:
#
# Every field ending in *_paise is already an integer paise value.
#
# Example:
# CSV input: "976.40"
# preprocessing: normalize_amount_to_paise("976.40") -> 97640
# canonical model: CanonicalBankRow(credit_paise=97640)
#
# Do NOT call normalize_amount_to_paise() inside these models.
# Doing so would multiply canonical paise values by 100 again.


class CanonicalMerchantRow(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: Optional[str] = None
    gross_amount_paise: int
    order_created_at: Optional[str] = None
    customer_reference: Optional[str] = None
    order_status: Optional[str] = None
    source_system: Optional[str] = None

    @field_validator(
        "merchant_order_id",
        "gateway_order_id",
        "customer_reference",
        mode="before",
    )
    @classmethod
    def normalize_optional_identifier_fields(cls, value):
        return normalize_identifier(value)

    @field_validator("order_created_at", mode="before")
    @classmethod
    def parse_order_created_at(cls, value):
        return parse_datetime_to_iso(value)

    @field_validator("order_status", "source_system", mode="before")
    @classmethod
    def clean_optional_text_fields(cls, value):
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @field_validator("gross_amount_paise", mode="before")
    @classmethod
    def validate_gross_amount_paise(cls, value):
        if value is None:
            raise ValueError("gross_amount_paise is required")

        integer_value = int(value)

        if integer_value < 0:
            raise ValueError("gross_amount_paise cannot be negative")

        return integer_value


class CanonicalRazorpayRow(BaseModel):
    transaction_entity: Optional[str] = None
    entity_id: str
    amount_paise: int
    currency: Optional[str] = None
    fee_paise: int = 0
    tax_paise: int = 0
    debit_paise: int = 0
    credit_paise: int = 0
    payment_method: Optional[str] = None
    entity_created_at: Optional[str] = None
    payment_captured_at: Optional[str] = None
    order_id: Optional[str] = None
    settlement_id: Optional[str] = None
    settled_at: Optional[str] = None
    settlement_utr: Optional[str] = None
    settled_by: Optional[str] = None

    @field_validator(
        "entity_id",
        "order_id",
        "settlement_id",
        "settlement_utr",
        "payment_method",
        "settled_by",
        "currency",
        mode="before",
    )
    @classmethod
    def normalize_identifier_fields(cls, value):
        return normalize_identifier(value)

    @field_validator("transaction_entity", mode="before")
    @classmethod
    def clean_transaction_entity(cls, value):
        if value is None:
            return None

        text = str(value).strip()
        return text.lower() or None

    @field_validator(
        "entity_created_at",
        "payment_captured_at",
        "settled_at",
        mode="before",
    )
    @classmethod
    def parse_date_fields(cls, value):
        return parse_datetime_to_iso(value)

    @field_validator(
        "amount_paise",
        "fee_paise",
        "tax_paise",
        "debit_paise",
        "credit_paise",
        mode="before",
    )
    @classmethod
    def validate_paise_fields(cls, value):
        if value is None:
            return 0

        integer_value = int(value)

        if integer_value < 0:
            raise ValueError("Razorpay paise fields cannot be negative")

        return integer_value


class CanonicalBankRow(BaseModel):
    transaction_date: Optional[str] = None
    value_date: Optional[str] = None
    description: Optional[str] = None
    reference_number: Optional[str] = None
    debit_paise: int = 0
    credit_paise: int = 0
    balance_paise: int = 0
    currency: Optional[str] = None
    utr: Optional[str] = None

    @field_validator("transaction_date", "value_date", mode="before")
    @classmethod
    def parse_date_fields(cls, value):
        return parse_datetime_to_iso(value)

    @field_validator(
        "reference_number",
        "utr",
        "currency",
        mode="before",
    )
    @classmethod
    def normalize_identifier_fields(cls, value):
        # Critical: this preserves a supplied/extracted UTR.
        # It does not overwrite it with None.
        return normalize_identifier(value)

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value):
        if value is None:
            return None

        text = str(value).strip()
        return text or None

    @field_validator("debit_paise", "credit_paise", mode="before")
    @classmethod
    def validate_nonnegative_paise_fields(cls, value):
        # These values are already paise; do not call
        # normalize_amount_to_paise() here.
        if value is None:
            return 0

        integer_value = int(value)

        if integer_value < 0:
            raise ValueError("debit_paise and credit_paise cannot be negative")

        return integer_value

    @field_validator("balance_paise", mode="before")
    @classmethod
    def validate_balance_paise(cls, value):
        # A bank balance can be negative for overdraft accounts,
        # so negative values are allowed.
        if value is None:
            return 0

        return int(value)


# Dead-letter / validation error model


class DeadLetterRow(BaseModel):
    row_index: int
    source: str
    error_code: str
    error_message: str
    raw_row: dict


# Match and exception result models


class MatchedLedgerRazorpay(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: str
    amount_paise: int
    match_method: str
    razorpay_entity_id: str
    razorpay_settlement_id: Optional[str] = None


class AmountMismatchRecord(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: str
    merchant_amount_paise: int
    razorpay_amount_paise: int
    razorpay_entity_id: str
    razorpay_settlement_id: Optional[str] = None
    match_method: str = "AMOUNT_MISMATCH"


class MatchedSettlementBank(BaseModel):
    settlement_id: str
    settlement_utr: Optional[str] = None
    expected_net_paise: int
    bank_credit_paise: int
    bank_reference: Optional[str] = None
    match_method: str


class SettlementAmountMismatch(BaseModel):
    settlement_id: str
    settlement_utr: Optional[str] = None
    expected_net_paise: int
    bank_credit_paise: int
    bank_reference: Optional[str] = None
    match_method: str = "SETTLEMENT_AMOUNT_MISMATCH"


class UnresolvedRecord(BaseModel):
    record_id: str
    source: str
    reason: str
    context: dict = Field(default_factory=dict)


class Stage3HandoffResponse(BaseModel):
    record: UnresolvedRecord
    status: Literal["POTENTIAL_FUZZY_MATCH", "EXCEPTION"]
    score: float
    amount_diff_paise: int
    date_diff_days: Optional[int] = None
    error_code: Optional[str] = None
    failed_gates: List[str] = Field(default_factory=list)
    reason: str

    source_record_id: Optional[str] = None
    candidate_record_id: Optional[str] = None
    review_evidence: Optional["Stage3ReviewEvidence"] = None


# Stage 1 internal result model


class ReconciliationResult(BaseModel):
    matched_ledger_razorpay: List[MatchedLedgerRazorpay] = Field(
        default_factory=list
    )
    amount_mismatches: List[AmountMismatchRecord] = Field(
        default_factory=list
    )
    matched_settlements_bank: List[MatchedSettlementBank] = Field(
        default_factory=list
    )
    settlement_amount_mismatches: List[SettlementAmountMismatch] = Field(
        default_factory=list
    )
    unresolved_records: List[UnresolvedRecord] = Field(
        default_factory=list
    )
    dead_letters: List[DeadLetterRow] = Field(
        default_factory=list
    )
    summary: dict = Field(default_factory=dict)


# Stage 3 LLM and reviewer-evidence models


Stage3LLMStatus = Literal[
    "STRONG_POTENTIAL_MATCH",
    "EXCEPTION",
    "NEEDS_MANUAL_REVIEW",
]

Stage3ReviewState = Literal[
    "PENDING_REVIEW",
    "MATCHED_BY_REVIEWER",
    "CONFIRMED_EXCEPTION",
    "INVESTIGATED",
]


class Stage3LLMOutput(BaseModel):
    """
    Strict JSON contract for Gemini.

    Gemini may recommend a strong potential match, explain an exception,
    or request manual review. It must never return MATCHED or RESOLVED.
    """

    status: Stage3LLMStatus
    error_code: Optional[
        Literal[
            "MISSING_SETTLEMENT_UTR",
            "DATE_OFFSET_EXCEEDED",
            "FEE_MISMATCH",
            "UNLINKED_ADJUSTMENT",
            "AMOUNT_MISMATCH",
            "OTHER",
        ]
    ] = None
    reasoning: str = Field(..., min_length=50, max_length=900)
    reported_amount_paise: Optional[int] = None
    source_record_token: Optional[str] = None
    candidate_record_token: Optional[str] = None
    human_approval_required: bool = False


class TokenMetadata(BaseModel):
    """
    Request-scoped metadata for a real identifier represented by an LLM token.

    Example:
    SETTLEMENT_001 -> real settlement ID, role="source",
    field="settlement_id", source="razorpay".
    """

    real_value: str
    role: Literal["source", "candidate", "secondary"]
    field: str
    source: Literal["merchant", "razorpay", "bank"]


class Stage3ComparisonEvidence(BaseModel):
    """
    Deterministic comparison values produced by Stage 2.

    Gemini may explain these values but cannot recalculate or override them.
    """

    similarity_score: float
    amount_diff_paise: int
    date_diff_days: Optional[int] = None
    failed_gates: List[str] = Field(default_factory=list)


class Stage3ReviewerLookup(BaseModel):
    """
    Trusted search keys for locating source rows in the uploaded CSV files.
    """

    merchant_csv_search: Optional[str] = None
    razorpay_csv_search: Optional[str] = None
    bank_csv_search: Optional[str] = None


class Stage3ReviewEvidence(BaseModel):
    """
    Authoritative reviewer-facing evidence.

    This evidence is built from trusted Stage 1 and Stage 2 data. It may
    contain real IDs for the internal reviewer UI. Gemini must later receive
    a separate masked/tokenized evidence payload.
    """

    exception_id: str
    comparison_type: str
    comparison_field: str
    source_record: Dict[str, Any] = Field(default_factory=dict)
    candidate_record: Dict[str, Any] = Field(default_factory=dict)
    comparison: Stage3ComparisonEvidence
    review_lookup: Stage3ReviewerLookup = Field(
        default_factory=Stage3ReviewerLookup
    )


class Stage3ExceptionResult(BaseModel):
    """
    Final Stage 3 backend result for one unresolved reconciliation item.

    Stage 1 and Stage 2 remain the deterministic financial source of truth.
    This model contains only the validated Gemini assistance and trusted
    reviewer evidence. It does not create an automatic financial match.
    """

    record_id: str
    source: str

    stage2_status: Literal["POTENTIAL_FUZZY_MATCH", "EXCEPTION"]
    stage2_error_code: Optional[str] = None
    stage2_failed_gates: List[str] = Field(default_factory=list)

    # Backward-compatible field used by the current Stage 3 implementation.
    grounding: Dict[str, Any] = Field(default_factory=dict)

    llm_status: Stage3LLMStatus
    llm_error_code: Optional[str] = None
    llm_reasoning: str
    llm_reported_amount_paise: Optional[int] = None
    llm_model_used: Optional[str] = None
    models_attempted: List[str] = Field(default_factory=list)

    ground_truth_amount_diff_paise: int
    numeric_cross_check_passed: bool

    # These will be populated when token validation is implemented.
    identifier_cross_check_passed: bool = False
    human_approval_required: bool = False

    # Trusted real IDs for the future internal frontend.
    source_record_id: Optional[str] = None
    candidate_record_id: Optional[str] = None
    review_evidence: Optional[Stage3ReviewEvidence] = None

    # Session-level future reviewer state only. It must not mutate Stage 1/2.
    review_state: Stage3ReviewState = "PENDING_REVIEW"

    used_fallback: bool = False
    fallback_reason: Optional[str] = None


# Combined Stage 1 + Stage 2 + Stage 3 API response model


class FullReconciliationResult(BaseModel):
    matched_ledger_razorpay: List[MatchedLedgerRazorpay] = Field(
        default_factory=list
    )
    fuzzy_ledger_matches: List[MatchedLedgerRazorpay] = Field(
        default_factory=list
    )
    amount_mismatches: List[AmountMismatchRecord] = Field(
        default_factory=list
    )
    matched_settlements_bank: List[MatchedSettlementBank] = Field(
        default_factory=list
    )
    fuzzy_settlement_matches: List[MatchedSettlementBank] = Field(
        default_factory=list
    )
    settlement_amount_mismatches: List[SettlementAmountMismatch] = Field(
        default_factory=list
    )
    stage3_handoffs: List[Stage3HandoffResponse] = Field(
        default_factory=list
    )
    stage3_results: List[Stage3ExceptionResult] = Field(
        default_factory=list
    )
    dead_letters: List[DeadLetterRow] = Field(
        default_factory=list
    )
    summary: dict = Field(default_factory=dict)

Stage3HandoffResponse.model_rebuild()