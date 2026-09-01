from typing import Optional, List
from pydantic import BaseModel, Field, field_validator

from .cleaners import (
    normalize_amount_to_paise,
    parse_datetime_to_iso,
    normalize_identifier,
    get_bank_reference_or_utr,
)


# Raw input models (for clarity)

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

class CanonicalMerchantRow(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: Optional[str] = None
    gross_amount_paise: int
    order_created_at: Optional[str] = None
    customer_reference: Optional[str] = None
    order_status: Optional[str] = None
    source_system: Optional[str] = None

    @field_validator("gross_amount_paise", mode="before")
    @classmethod
    def validate_gross_amount_paise(cls, v):
        return normalize_amount_to_paise(v)

    @field_validator("gateway_order_id", "merchant_order_id", "customer_reference", mode="before")
    @classmethod
    def normalize_optional_id(cls, v):
        return normalize_identifier(v)

    @field_validator("order_created_at", mode="before")
    @classmethod
    def parse_order_created_at(cls, v):
        return parse_datetime_to_iso(v)


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

    @field_validator("amount_paise", "fee_paise", "tax_paise", "debit_paise", "credit_paise", mode="before")
    @classmethod
    def validate_amount_fields(cls, v):
        if v is None:
            return 0
        return normalize_amount_to_paise(v)

    @field_validator(
        "entity_id",
        "order_id",
        "settlement_id",
        "settlement_utr",
        "payment_method",
        "settled_by",
        mode="before",
    )
    @classmethod
    def normalize_optional_id(cls, v):
        return normalize_identifier(v)

    @field_validator("entity_created_at", "payment_captured_at", "settled_at", mode="before")
    @classmethod
    def parse_date_fields(cls, v):
        return parse_datetime_to_iso(v)


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

    @field_validator("debit_paise", "credit_paise", "balance_paise", mode="before")
    @classmethod
    def validate_amount_fields(cls, v):
        if v is None:
            return 0
        return normalize_amount_to_paise(v)

    @field_validator("utr", mode="before")
    @classmethod
    def compute_utr(cls, v, info):
        # v here is the raw utr field if we decide to pass one; for MVP we compute from reference+description
        return None  # will be set explicitly in parsing logic using get_bank_reference_or_utr

    @field_validator("transaction_date", "value_date", mode="before")
    @classmethod
    def parse_date_fields(cls, v):
        return parse_datetime_to_iso(v)

    @field_validator("reference_number", "description", mode="before")
    @classmethod
    def normalize_text(cls, v):
        if v is None:
            return None
        return str(v).strip()


# Dead-letter / validation error model

class DeadLetterRow(BaseModel):
    row_index: int
    source: str  # "merchant", "razorpay", "bank"
    error_code: str  # e.g. "SCHEMA_VALIDATION_FAILED"
    error_message: str
    raw_row: dict


# Matched and exception result models

class MatchedLedgerRazorpay(BaseModel):
    merchant_order_id: Optional[str] = None
    gateway_order_id: str
    amount_paise: int
    match_method: str  # "EXACT_ORDER_ID"
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
    match_method: str  # "EXACT_UTR"


class SettlementAmountMismatch(BaseModel):
    settlement_id: str
    settlement_utr: Optional[str] = None
    expected_net_paise: int
    bank_credit_paise: int
    bank_reference: Optional[str] = None
    match_method: str = "SETTLEMENT_AMOUNT_MISMATCH"


class UnresolvedRecord(BaseModel):
    record_id: str
    source: str  # "ledger", "razorpay", "bank"
    reason: str  # e.g. "NO_EXACT_ORDER_ID", "NO_EXACT_UTR"
    context: dict = Field(default_factory=dict)


# Final reconciliation result

class ReconciliationResult(BaseModel):
    matched_ledger_razorpay: List[MatchedLedgerRazorpay] = Field(default_factory=list)
    amount_mismatches: List[AmountMismatchRecord] = Field(default_factory=list)
    matched_settlements_bank: List[MatchedSettlementBank] = Field(default_factory=list)
    settlement_amount_mismatches: List[SettlementAmountMismatch] = Field(default_factory=list)
    unresolved_records: List[UnresolvedRecord] = Field(default_factory=list)
    dead_letters: List[DeadLetterRow] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)