"""Parser for ING bank account CSV exports.

Format: semicolon-separated, German locale numbers (1.234,56).
Multi-section file with metadata header before the "Kontoumsätze" section.
"""

import csv
import io
import re
from dataclasses import dataclass


@dataclass
class Transaction:
    buchung: str  # "DD.MM.YYYY"
    valuta: str  # "DD.MM.YYYY" or "vorgemerkt"
    sender: str  # name of sender/recipient
    verwendungszweck: str
    betrag: float  # positive = incoming, negative = outgoing
    raw_betrag: str  # original string e.g. "2.385,60"


def parse_german_number(s: str) -> float:
    """Convert German locale number string to float. E.g. '2.385,60' -> 2385.60"""
    s = s.strip()
    s = s.replace(".", "").replace(",", ".")
    return float(s)


_MELLON_KEYWORDS = re.compile(
    r"mellon|plx|plex|youtube|minecraft|\byt\b", re.IGNORECASE
)


def is_mellon(tx: Transaction) -> bool:
    """Auto-detect Mellon donations from Verwendungszweck."""
    return tx.betrag > 0 and bool(_MELLON_KEYWORDS.search(tx.verwendungszweck))


def parse_ing_csv(content: str | bytes) -> list[Transaction]:
    """Parse an ING bank statement CSV and return only incoming transactions."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")

    # Split into sections; we only care about Kontoumsätze
    sections = re.split(r"\n(?=Kontoumsätze)", content)
    if len(sections) < 2:
        raise ValueError("CSV does not contain a 'Kontoumsätze' section")

    umsaetze_section = sections[-1]

    # Find the header line within the Kontoumsätze section
    lines = umsaetze_section.strip().split("\n")
    header_idx = None
    for i, line in enumerate(lines):
        if "Buchung" in line and "Valuta" in line:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find transaction header in 'Kontoumsätze' section")

    data_lines = lines[header_idx:]
    reader = csv.DictReader(data_lines, delimiter=";")

    # Strip trailing spaces from field names (ING CSV has "Buchung " etc.)
    if reader.fieldnames:
        reader.fieldnames = [f.strip() for f in reader.fieldnames]

    transactions = []
    for row in reader:
        # Skip rows without a booking date
        buchung = row.get("Buchung", "").strip()
        if not buchung:
            continue

        betrag_str = row.get("Betrag", "0").strip()
        try:
            betrag = parse_german_number(betrag_str)
        except (ValueError, TypeError):
            continue

        # Only incoming (positive) transactions
        if betrag <= 0:
            continue

        sender = row.get("Sender / Empfänger", "").strip()
        verwendungszweck = row.get("Verwendungszweck", "").strip()

        transactions.append(
            Transaction(
                buchung=buchung,
                valuta=row.get("Valuta", "").strip(),
                sender=sender,
                verwendungszweck=verwendungszweck,
                betrag=betrag,
                raw_betrag=betrag_str,
            )
        )

    return transactions


def transaction_key(tx: Transaction) -> str:
    """Unique key for deduplication: date + normalized amount + sender."""
    return f"{tx.buchung}|{tx.betrag:.2f}|{tx.sender.lower()}"
