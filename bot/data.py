from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Market, MarketSnapshot, OrderBook, Quote
from risk.core import D, ZERO


@dataclass(frozen=True)
class DataConfig:
    gamma_url: str = "https://gamma-api.polymarket.com"
    clob_url: str = "https://clob.polymarket.com"
    timeout_seconds: float = 10.0
    user_agent: str = "polymarket-risk-bot/0.1"


class PublicDataClient:
    """Read-only Gamma/CLOB adapter. It never signs or submits orders."""

    def __init__(self, config: DataConfig | None = None):
        self.config = config or DataConfig()

    def list_markets(self, limit: int = 100, active: bool = True) -> list[Market]:
        query = urlencode({"limit": limit, "active": str(active).lower(), "closed": "false"})
        payload = self._get_json(f"{self.config.gamma_url.rstrip('/')}/markets?{query}")
        rows = payload if isinstance(payload, list) else payload.get("markets", payload.get("data", []))
        markets: list[Market] = []
        for row in rows:
            market = self._parse_market(row)
            if market is not None:
                markets.append(market)
        return markets

    def get_order_book(self, token_id: str, market_id: str = "") -> OrderBook:
        query = urlencode({"token_id": token_id})
        payload = self._get_json(f"{self.config.clob_url.rstrip('/')}/book?{query}")
        bids = tuple(self._parse_quote(level) for level in payload.get("bids", []))
        asks = tuple(self._parse_quote(level) for level in payload.get("asks", []))
        return OrderBook(
            market_id=market_id,
            token_id=token_id,
            bids=bids,
            asks=asks,
            captured_at=datetime.now(timezone.utc),
            metadata_hash=self._hash_payload(payload),
        )

    def snapshot(self, market: Market) -> MarketSnapshot:
        book = self.get_order_book(market.token_id, market.market_id)
        bid = book.best_bid.price if book.best_bid else ZERO
        ask = book.best_ask.price if book.best_ask else ZERO
        midpoint = (bid + ask) / Decimal("2") if bid and ask else (bid or ask)
        return MarketSnapshot(market=market, order_book=book, midpoint=midpoint)

    def _get_json(self, url: str) -> dict | list:
        request = Request(url, headers={"User-Agent": self.config.user_agent, "Accept": "application/json"})
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    @staticmethod
    def _parse_quote(level: dict | list) -> Quote:
        if isinstance(level, dict):
            return Quote(price=D(level.get("price", "0")), size=D(level.get("size", "0")))
        return Quote(price=D(level[0]), size=D(level[1]))

    @staticmethod
    def _parse_market(row: dict) -> Market | None:
        market_id = str(row.get("conditionId") or row.get("condition_id") or row.get("id") or "")
        question = str(row.get("question") or row.get("title") or "")
        if not market_id or not question:
            return None
        tokens = row.get("tokens") or row.get("clobTokenIds") or []
        if isinstance(tokens, str):
            try:
                tokens = json.loads(tokens)
            except json.JSONDecodeError:
                tokens = [tokens]
        outcomes = row.get("outcomes") or []
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = [outcomes]
        token_id = ""
        outcome = str(row.get("outcome") or "")
        if isinstance(tokens, list) and tokens:
            first = tokens[0]
            if isinstance(first, dict):
                token_id = str(first.get("token_id") or first.get("tokenId") or first.get("id") or "")
                outcome = outcome or str(first.get("outcome") or "")
            else:
                token_id = str(first)
                if isinstance(outcomes, list) and outcomes:
                    outcome = outcome or str(outcomes[0])
        if not token_id:
            token_id = str(row.get("token_id") or row.get("clobTokenId") or "")
        if not token_id:
            return None
        event_id = str(row.get("eventId") or row.get("event_id") or row.get("groupItemTitle") or market_id)
        resolution_source = str(row.get("resolutionSource") or row.get("resolution_source") or "unknown")
        risk = "AMBIGUOUS" if not resolution_source or resolution_source == "unknown" else "VERIFIED"
        augmented = bool(row.get("negRiskAugmented") or row.get("augmentedNegRisk"))
        metadata_hash = hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode()).hexdigest()
        return Market(
            market_id=market_id,
            event_id=event_id,
            question=question,
            outcome=outcome or "unknown",
            token_id=token_id,
            resolution_source=resolution_source,
            resolution_risk=risk,
            correlation_group=event_id,
            active=bool(row.get("active", True)),
            neg_risk=bool(row.get("negRisk") or row.get("negativeRisk")),
            augmented_neg_risk=augmented,
            metadata_hash=metadata_hash,
        )

    @staticmethod
    def _hash_payload(payload: dict) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
