"""
Parser for Telegram buy signal messages
"""
import re
import logging

logger = logging.getLogger(__name__)

# Regex pour adresses Solana Base58 (32-44 caractères)
BASE58_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')


def parse_signal(text: str, source_channel: str) -> dict | None:
    """
    Parse a buy signal message and extract all relevant fields.

    Returns dict with:
    - wallet_name
    - wallet_address (optional)
    - contract_address
    - signal_mc_usd
    - signal_lq_usd
    - seen_minutes
    - source_channel

    Returns None if parsing fails.
    """
    if not text:
        return None

    # Vérifier que c'est un signal BUY
    if "🟢 BUY" not in text:
        return None

    result = {
        "source_channel": source_channel,
        "wallet_name": None,
        "wallet_address": None,
        "contract_address": None,
        "signal_mc_usd": None,
        "signal_lq_usd": None,
        "seen_minutes": None,
    }

    lines = text.strip().split('\n')

    # Extraire wallet_name (ligne après 🔹)
    for i, line in enumerate(lines):
        if line.startswith('🔹'):
            result["wallet_name"] = line.replace('🔹', '').strip()

            # Wallet address est souvent la ligne suivante (si c'est une adresse Base58)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                wallet_match = BASE58_PATTERN.match(next_line)
                if wallet_match and len(next_line) < 50:  # Juste l'adresse, pas plus
                    result["wallet_address"] = wallet_match.group()
            break

    # Extraire toutes les adresses Base58
    all_addresses = BASE58_PATTERN.findall(text)

    if not all_addresses:
        logger.warning("No contract address found in message")
        return None

    # Le contract_address est la DERNIÈRE adresse (après "Seen:")
    result["contract_address"] = all_addresses[-1]

    # Extraire MC (Market Cap)
    mc_match = re.search(r'MC:\s*\$([0-9,.]+)([KMB]?)', text, re.IGNORECASE)
    if mc_match:
        result["signal_mc_usd"] = parse_value_with_suffix(mc_match.group(1), mc_match.group(2))

    # Extraire LQ (Liquidity)
    lq_match = re.search(r'LQ:\s*\$([0-9,.]+)([KMB]?)', text, re.IGNORECASE)
    if lq_match:
        result["signal_lq_usd"] = parse_value_with_suffix(lq_match.group(1), lq_match.group(2))

    # Extraire Seen (âge du token)
    seen_match = re.search(r'Seen:\s*([0-9]+d)?\s*([0-9]+h)?', text)
    if seen_match:
        result["seen_minutes"] = parse_seen_to_minutes(seen_match.group(1), seen_match.group(2))

    # Validation : on doit avoir au minimum contract_address et wallet_name
    if not result["contract_address"]:
        logger.warning("Missing contract_address")
        return None

    if not result["wallet_name"]:
        logger.warning("Missing wallet_name")
        return None

    return result


def parse_value_with_suffix(value_str: str, suffix: str) -> float:
    """Convert value with K/M/B suffix to float"""
    try:
        # Remove commas
        value = float(value_str.replace(',', ''))

        suffix = suffix.upper() if suffix else ''

        if suffix == 'K':
            value *= 1_000
        elif suffix == 'M':
            value *= 1_000_000
        elif suffix == 'B':
            value *= 1_000_000_000

        return value
    except (ValueError, TypeError):
        return None


def parse_seen_to_minutes(days_str: str | None, hours_str: str | None) -> int:
    """Convert '5d 23h' format to total minutes"""
    total_minutes = 0

    if days_str:
        days = int(days_str.replace('d', ''))
        total_minutes += days * 24 * 60

    if hours_str:
        hours = int(hours_str.replace('h', ''))
        total_minutes += hours * 60

    return total_minutes if total_minutes > 0 else None


def extract_symbol_from_message(text: str) -> str | None:
    """Extract token symbol from message (after BUY or in the link line)"""
    # Try from 🔗 line: "| ALIENS |" pattern
    symbol_match = re.search(r'\|\s*([A-Z0-9$]+)\s*\|', text)
    if symbol_match:
        return symbol_match.group(1)

    # Try from BUY line
    buy_match = re.search(r'🟢 BUY\s+(\S+)', text)
    if buy_match:
        return buy_match.group(1).strip('()')

    return None
