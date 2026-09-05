from decimal import Decimal, InvalidOperation
from datetime import datetime
import re
from typing import Optional

from dateutil import parser as dateutil_parser


DATE_INPUT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d",
)

PAISE_PER_RUPEE = 100

# UTRs such as HDFCINBB20260829001234, ICICR20260830007891.
UTR_PATTERN = re.compile(r"\b[A-Z0-9]{12,22}\b")

# Currency prefixes that may contain punctuation (notably "Rs.").
_CURRENCY_PREFIX_RE = re.compile(
    r"^\s*(?:₹|rs\.?|inr)\s*",
    flags=re.IGNORECASE,
)

# Complete numeric token validation. Reject arbitrary text instead of silently
# stripping letters from a malformed amount.
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")


def normalize_amount_to_paise(raw: object) -> int:
    """
    Convert a raw rupee amount into integer paise.

    Supports common banking/export formats such as:
    - 1,250.00
    - ₹1,250.00
    - Rs. 11,130.82
    - INR 2,199.90
    - -320.00
    - (320.00)

    Missing or malformed values raise ValueError. The sign is preserved so
    refund/adjustment rows can participate correctly in settlement aggregation.
    """
    if raw is None:
        raise ValueError("Amount is required")

    value = str(raw).strip()
    if value.lower() in {"", "nan", "none", "null"}:
        raise ValueError("Amount is required")

    value = _CURRENCY_PREFIX_RE.sub("", value)

    negative_parentheses = value.startswith("(") and value.endswith(")")
    if negative_parentheses:
        value = value[1:-1].strip()

    value = value.replace(",", "").replace(" ", "")

    if not _NUMERIC_RE.fullmatch(value):
        raise ValueError(f"Invalid amount: {raw}")

    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {raw}") from exc

    if negative_parentheses:
        decimal_value = -decimal_value

    # Financial pipeline contract: all downstream arithmetic is integer paise.
    return int(decimal_value * PAISE_PER_RUPEE)


def parse_datetime_to_iso(raw: Optional[str]) -> Optional[str]:
    """Parse a common CSV date/time representation into an ISO string."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None

    raw_text = str(raw).strip()

    for fmt in DATE_INPUT_FORMATS:
        try:
            dt = datetime.strptime(raw_text, fmt)
            return dt.isoformat()
        except ValueError:
            continue

    try:
        dt = dateutil_parser.parse(raw_text)
        return dt.isoformat()
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Unrecognized date format: {raw_text}") from exc


def normalize_identifier(raw: object) -> Optional[str]:
    """Trim whitespace and uppercase an optional identifier."""
    if raw is None:
        return None

    text = str(raw).strip()
    if not text or text.lower() in {"nan", "none", "null"}:
        return None

    return text.upper()


def extract_utr_from_description(description: Optional[str]) -> Optional[str]:
    """Extract the first UTR-shaped alphanumeric reference from a narration."""
    if description is None:
        return None

    text = str(description).upper()
    for candidate in UTR_PATTERN.findall(text):
        if any(ch.isdigit() for ch in candidate) and any(ch.isalpha() for ch in candidate):
            return candidate

    return None


def get_bank_reference_or_utr(
    reference_number: Optional[str],
    description: Optional[str],
) -> Optional[str]:
    """
    Prefer the bank's explicit reference_number, otherwise extract a UTR from
    the narration. This remains deterministic Stage 1 logic.
    """
    reference = normalize_identifier(reference_number)
    if reference:
        return reference

    return extract_utr_from_description(description)
