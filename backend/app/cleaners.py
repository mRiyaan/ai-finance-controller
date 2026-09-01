from decimal import Decimal, InvalidOperation
from datetime import datetime
import re
from typing import Optional
from dateutil import parser as dateutil_parser


# Unified date formats list (used by parse_datetime_to_iso)
DATE_INPUT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",      # 2026-08-27 10:15:00
    "%Y-%m-%d",               # 2026-08-27
    "%d/%m/%Y %H:%M:%S",      # 27/08/2026 10:15:00
    "%d/%m/%Y",               # 27/08/2026
    "%m/%d/%Y %H:%M:%S",      # 08/27/2026 10:15:00
    "%m/%d/%Y",               # 08/27/2026
    "%Y-%m-%dT%H:%M:%S",      # 2026-08-27T10:15:00
    "%Y-%m-%dT%H:%M:%SZ",     # 2026-08-27T10:15:00Z
    "%d-%m-%Y %H:%M:%S",      # 27-08-2026 10:15:00
    "%d-%m-%Y",               # 27-08-2026
    "%Y/%m/%d %H:%M:%S",      # 2026/08/27 10:15:00
    "%Y/%m/%d",               # 2026/08/27
)

PAISE_PER_RUPEE = 100


def normalize_amount_to_paise(raw: Optional[str]) -> int:
    """
    Convert a raw amount string (e.g. '1,250.00', '₹1,250.00') to integer paise.

    Raises ValueError if the input cannot be parsed.
    """
    if raw is None:
        raise ValueError("Amount is required")

    value = str(raw).strip()
    if value == "":
        raise ValueError("Amount is empty")

    # Remove thousands separators
    value = value.replace(",", "")

    # Remove non-numeric / non-dot characters such as currency symbols
    value = re.sub(r"[^\d\.]", "", value)

    try:
        decimal_value = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid amount: {raw}") from exc

    paise = int(decimal_value * PAISE_PER_RUPEE)
    return paise


def parse_datetime_to_iso(raw: Optional[str]) -> Optional[str]:
    """
    Parse a date/time string from various common formats and return an ISO string.

    Returns None for None or empty strings.
    Raises ValueError if the format cannot be recognized.
    """
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return None

    raw = raw.strip()

    # Try explicit formats first (fast and deterministic)
    for fmt in DATE_INPUT_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.isoformat()
        except ValueError:
            continue

    # Fallback: dateutil parser for flexible, real-world CSV formats
    try:
        dt = dateutil_parser.parse(raw)
        return dt.isoformat()
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"Unrecognized date format: {raw}")


def normalize_identifier(raw: Optional[str]) -> Optional[str]:
    """
    Trim whitespace and normalize an identifier/UTR/order ID to uppercase.

    Returns None for blank/None inputs.
    """
    if raw is None:
        return None

    text = str(raw).strip()
    if text == "":
        return None

    return text.upper()


# UTRs such as HDFCINBB20260829001234, ICICR20260830007891:
# uppercase alphanumeric, typically 16–22 characters.
UTR_PATTERN = re.compile(r"\b[A-Z0-9]{12,22}\b")


def extract_utr_from_description(description: Optional[str]) -> Optional[str]:
    """
    Deterministically extract a UTR-shaped string from a bank narration/description.

    Returns the first candidate containing both letters and digits, or None if none found.
    """
    if description is None:
        return None

    text = str(description).upper()
    matches = UTR_PATTERN.findall(text)

    for candidate in matches:
        # Require at least one letter and one digit to avoid plain words or numbers
        if any(ch.isdigit() for ch in candidate) and any(ch.isalpha() for ch in candidate):
            return candidate

    return None


def get_bank_reference_or_utr(reference_number: Optional[str],
                              description: Optional[str]) -> Optional[str]:
    """
    Use the explicit bank reference_number if present; otherwise fall back
    to regex extraction from description/narration.

    This is still deterministic Stage 1 matching logic.
    """
    ref = normalize_identifier(reference_number)
    if ref:
        return ref

    return extract_utr_from_description(description)