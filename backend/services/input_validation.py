import ipaddress
import re


def is_valid_simulated_recipient(recipient, known_recipients):
    if not recipient:
        return False
    if recipient in known_recipients:
        return True

    cleaned_phone = re.sub(r"[\s\-()]", "", str(recipient))
    if re.fullmatch(r"(?:02|05|03)\d{8}|\+233(?:2|5|3)\d{8}", cleaned_phone):
        return True

    # Structured wallet or bank references
    if re.fullmatch(
        r"(?:WALLET|BANK-ACC|CUSTOM|MOMO)-[A-Za-z0-9_-]{2,15}",
        str(recipient),
        re.IGNORECASE,
    ):
        return True

    # Single-token alphanumeric wallet identifiers (4-25 chars)
    # Reject multi-word names with spaces like "recipient name" unless registered
    if re.fullmatch(r"[A-Za-z0-9_-]{4,25}", str(recipient)) and not str(recipient).upper().startswith("BANK-ACC"):
        return True

    return False


def is_valid_wallet_reference(reference):
    """A wallet reference is a demo/typed wallet ID, Ghana mobile number, or custom identifier.

    Bank accounts are NOT wallet references, so ``BANK-ACC-*`` is excluded.
    """
    if not reference:
        return False

    cleaned_phone = re.sub(r"[\s\-()]", "", str(reference))
    if re.fullmatch(r"(?:02|05|03)\d{8}|\+233(?:2|5|3)\d{8}", cleaned_phone):
        return True

    if re.fullmatch(
        r"(?:WALLET|CUSTOM|MOMO|USER)-[A-Za-z0-9_-]{2,15}",
        str(reference),
        re.IGNORECASE,
    ):
        return True

    # Single-token alphanumeric identifier
    if re.fullmatch(r"[A-Za-z0-9_-]{3,25}", str(reference)) and not str(reference).upper().startswith("BANK-ACC"):
        return True

    return False


def validate_ip_address(value):
    try:
        return str(ipaddress.ip_address(value))
    except ValueError as error:
        raise ValueError(
            "Enter a valid IPv4 or IPv6 address, for example 192.168.1.22."
        ) from error
