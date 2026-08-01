"""Parser for PayPal CSV activity exports.

Format: comma-separated with double-quote quoting, German locale numbers (1.234,56).
One flat list of all transactions (incoming, outgoing, internal).
"""

import csv
import io
import re
from dataclasses import dataclass


@dataclass
class PaypalTransaction:
    datum: str  # "DD.MM.YYYY"
    uhrzeit: str  # "HH:MM:SS"
    beschreibung: str  # e.g. "Handyzahlung", "PayPal Express-Zahlung"
    brutto: float  # positive = incoming, negative = outgoing
    netto: float  # after fees
    entgelt: float  # fee
    name: str  # sender/recipient name
    email: str  # sender email
    transaktionscode: str


# PayPal transaction types that are internal / not real income
_INTERNAL_TYPES = {
    "Bankgutschrift auf PayPal-Konto",
    "Allgemeine Währungsumrechnung",
    "Allgemeine interne Kontobewegung",
    "Rückbuchung allgemeiner Einbehaltung",
    "Einbehaltung für offene Autorisierung",
}

_PAYPAL_MELLON_KEYWORDS = re.compile(
    r"mellon|plx|plex|youtube|minecraft|\byt\b", re.IGNORECASE
)


def parse_german_number(s: str) -> float:
    """Convert German locale number string to float. E.g. '2.385,60' -> 2385.60"""
    s = s.strip().strip('"')
    if s in ("", "-"):
        return 0.0
    s = s.replace(".", "").replace(",", ".")
    return float(s)


def is_mellon_paypal(tx: PaypalTransaction) -> bool:
    """Auto-detect Mellon donations from transaction description or sender."""
    search_text = f"{tx.beschreibung} {tx.name} {tx.email}"
    return bool(_PAYPAL_MELLON_KEYWORDS.search(search_text))


def parse_paypal_csv(content: str | bytes) -> list[PaypalTransaction]:
    """Parse a PayPal CSV and return only actual incoming payments."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")
    else:
        # Remove BOM if present
        content = content.lstrip("\ufeff")

    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames:
        reader.fieldnames = [f.strip().strip('"') for f in reader.fieldnames]

    transactions = []
    for row in reader:
        beschreibung = row.get("Beschreibung", "").strip()
        if not beschreibung:
            continue

        # Skip internal PayPal transactions
        if beschreibung in _INTERNAL_TYPES:
            continue

        try:
            brutto = parse_german_number(row.get("Brutto", "0"))
            netto = parse_german_number(row.get("Netto", "0"))
            entgelt = parse_german_number(row.get("Entgelt", "0"))
        except (ValueError, TypeError):
            continue

        # Only incoming (positive) payments
        if brutto <= 0:
            continue

        datum = row.get("Datum", "").strip()
        if not datum:
            continue

        transactions.append(
            PaypalTransaction(
                datum=datum,
                uhrzeit=row.get("Uhrzeit", "").strip(),
                beschreibung=beschreibung,
                brutto=brutto,
                netto=round(netto, 2),
                entgelt=round(entgelt, 2),
                name=row.get("Name", "").strip(),
                email=row.get("Absender E-Mail-Adresse", "").strip(),
                transaktionscode=row.get("Transaktionscode", "").strip(),
            )
        )

    return transactions


def paypal_transaction_key(tx: PaypalTransaction) -> str:
    """Unique key for deduplication."""
    return f"{tx.datum}|{tx.brutto:.2f}|{tx.name.lower()}"
