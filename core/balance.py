#!/usr/bin/env python3
"""balance.py -- query DeepSeek wallet balance for the status bar.

Direct port of Reasonix internal/billing/balance.go.  Queries
GET /user/balance with Bearer auth, normalizes the response
into a Balance dataclass with compact display formatting.

The balance URL is derived from the API base URL: if the chat
endpoint is https://api.deepseek.com/v1/chat/completions, the
balance endpoint is https://api.deepseek.com/user/balance.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field


@dataclass
class BalanceInfo:
    """One currency's balance entry."""

    currency: str  # "CNY" | "USD"
    total_balance: str  # total available (granted + topped-up)
    granted_balance: str  # unexpired promotional credit
    topped_up_balance: str  # paid-in credit


@dataclass
class Balance:
    """Wallet balance normalized for display."""

    available: bool = False
    infos: list[BalanceInfo] = field(default_factory=list)

    @property
    def display(self) -> str:
        """Render the primary balance compactly, e.g. '¥110.00'.

        Prefers CNY, then the first currency reported.
        Returns '' when there's nothing to show.
        """
        if not self.infos:
            return ""
        pick = self.infos[0]
        for info in self.infos:
            if info.currency.upper() == "CNY":
                pick = info
                break
        symbol = _currency_symbol(pick.currency)
        return symbol + pick.total_balance.strip()


def _currency_symbol(currency: str) -> str:
    """Map ISO currency code to display symbol."""
    cur = currency.upper().strip()
    if cur in ("CNY", "RMB"):
        return "\u00a5"  # ¥
    if cur == "USD":
        return "$"
    if cur:
        return cur + " "
    return ""


def _balance_url_from_api_url(api_url: str) -> str:
    """Derive the balance endpoint from the chat API URL.

    Example: https://api.deepseek.com/v1/chat/completions
          -> https://api.deepseek.com/user/balance
    """
    from urllib.parse import urlparse

    parsed = urlparse(api_url)
    return f"{parsed.scheme}://{parsed.netloc}/user/balance"


def fetch_balance(api_url: str, api_key: str, timeout: float = 12.0) -> Balance | None:
    """Query the DeepSeek balance endpoint.

    Args:
        api_url: The chat API URL (e.g. https://api.deepseek.com/v1/chat/completions).
        api_key: The Bearer token.
        timeout: HTTP timeout in seconds (default 12s).

    Returns:
        Balance on success, None if the endpoint is unreachable or the
        response is unparseable.
    """
    if not api_url or not api_key:
        return None
    balance_url = _balance_url_from_api_url(api_url)
    req = urllib.request.Request(
        balance_url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except Exception:
        return None

    try:
        js = json.loads(body)
    except json.JSONDecodeError:
        return None

    available = bool(js.get("is_available", False))
    infos_raw = js.get("balance_infos", [])
    if not isinstance(infos_raw, list):
        return Balance(available=available)

    infos: list[BalanceInfo] = []
    for entry in infos_raw:
        if not isinstance(entry, dict):
            continue
        infos.append(
            BalanceInfo(
                currency=str(entry.get("currency", "")),
                total_balance=str(entry.get("total_balance", "0")),
                granted_balance=str(entry.get("granted_balance", "0")),
                topped_up_balance=str(entry.get("topped_up_balance", "0")),
            )
        )
    return Balance(available=available, infos=infos)
