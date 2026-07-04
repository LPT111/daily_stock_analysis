# -*- coding: utf-8 -*-
"""Market structure context composer for stock reports."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

from data_provider import DataFetcherManager

from src.schemas.market_structure import (
    MARKET_STRUCTURE_SCHEMA_VERSION,
    MARKET_THEME_SCHEMA_VERSION,
    STOCK_MARKET_POSITION_SCHEMA_VERSION,
    MarketStructureContext,
    MarketStructureDataQuality,
    MarketStructureRiskTag,
    MarketStructureSource,
    MarketThemeContext,
    PrimaryTheme,
    StockBoardPosition,
    StockMarketPosition,
    ThemePhase,
    ThemeRankSource,
    dump_market_structure_model,
)
from src.services.market_hotspot_service import MarketHotspotService
from src.utils.data_processing import extract_board_detail_fields


logger = logging.getLogger(__name__)

_VALID_THEME_SOURCES = {"industry", "concept", "mixed", "unknown"}
_VALID_THEME_PHASES = {"warming", "accelerating", "cooling", "unknown"}


class MarketStructureService:
    """Compose market-theme and stock-position layers into one context."""

    def __init__(
        self,
        fetcher_manager: Optional[DataFetcherManager] = None,
        hotspot_service: Optional[MarketHotspotService] = None,
    ) -> None:
        self.fetcher_manager = fetcher_manager or DataFetcherManager()
        self.hotspot_service = hotspot_service or MarketHotspotService(
            fetcher_manager=self.fetcher_manager,
        )

    def build_context(
        self,
        *,
        code: str,
        stock_name: Optional[str],
        market: str,
        fundamental_context: Optional[Dict[str, Any]],
        trade_date: Any = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_market = str(market or "cn").strip().lower() or "cn"
        trade_date_text = self._resolve_trade_date(trade_date, market_phase_summary)
        stock_code = str(code or "").strip()

        if normalized_market != "cn":
            theme_context = MarketThemeContext(
                status="not_supported",
                market=normalized_market,
                trade_date=trade_date_text,
                data_quality=MarketStructureDataQuality(
                    status="not_supported",
                    missing_fields=["a_share_theme_context"],
                    sources=[
                        MarketStructureSource(
                            provider="dsa",
                            dataset="market_structure",
                            status="not_supported",
                            message="stock market structure is only supported for A-share first version",
                        )
                    ],
                ),
            )
            stock_position = StockMarketPosition(
                status="not_supported",
                stock_code=stock_code,
                stock_name=stock_name,
                market=normalized_market,
                missing_fields=["a_share_theme_context"],
            )
            return dump_market_structure_model(
                MarketStructureContext(
                    status="not_supported",
                    market=normalized_market,
                    trade_date=trade_date_text,
                    market_theme_context=theme_context,
                    stock_market_position=stock_position,
                )
            )

        board_details = extract_board_detail_fields(
            {"fundamental_context": fundamental_context or {}}
        )
        sector_rankings_payload = board_details.get("sector_rankings")
        concept_rankings_payload = board_details.get("concept_rankings")
        sector_rankings = sector_rankings_payload or {}
        concept_rankings = concept_rankings_payload or {}
        market_theme_payload = self.hotspot_service.get_hotspots(
            market=normalized_market,
            trade_date=trade_date_text,
            sector_rankings=sector_rankings_payload,
            concept_rankings=concept_rankings_payload,
        )
        market_theme_context = MarketThemeContext.model_validate(market_theme_payload)

        related_boards = self._build_related_boards(
            board_details.get("belong_boards") or [],
            sector_rankings=sector_rankings,
            concept_rankings=concept_rankings,
        )
        primary_theme = self._infer_primary_theme(market_theme_payload, related_boards)
        stock_role = self._infer_stock_role(primary_theme, related_boards)
        theme_phase: ThemePhase = primary_theme.phase if primary_theme is not None else "unknown"
        has_primary_market_evidence = self._has_primary_market_evidence(primary_theme)

        missing_fields = ["hotspot_constituents", "leader_stocks"]
        risk_tags: List[MarketStructureRiskTag] = []
        if market_theme_context.status != "ok":
            risk_tags.append(
                MarketStructureRiskTag(
                    code="theme_data_partial",
                    message="市场题材数据不完整，题材强弱仅作降级参考",
                )
            )
        if related_boards and not has_primary_market_evidence:
            missing_fields.append("theme_ranking_match")
            risk_tags.append(
                MarketStructureRiskTag(
                    code="stock_theme_evidence_partial",
                    message="个股板块未匹配到市场题材榜单，个股位置按降级证据处理",
                )
            )
        if not related_boards:
            missing_fields.append("belong_boards")
            risk_tags.append(
                MarketStructureRiskTag(
                    code="board_membership_missing",
                    message="缺少个股所属板块证据，无法判断题材位置",
                )
            )

        if primary_theme is not None and related_boards and has_primary_market_evidence:
            stock_status = "ok"
        elif primary_theme is not None or related_boards:
            stock_status = "partial"
        else:
            stock_status = "unknown"

        if market_theme_context.status == "ok" and stock_status == "ok":
            combined_status = "ok"
        elif market_theme_context.status in {"ok", "partial"} or stock_status in {"ok", "partial"}:
            combined_status = "partial"
        else:
            combined_status = "unknown"

        stock_position = StockMarketPosition(
            status=stock_status,
            stock_code=stock_code,
            stock_name=stock_name,
            market=normalized_market,
            primary_theme=primary_theme,
            related_boards=related_boards,
            stock_role=stock_role,
            theme_phase=theme_phase,
            risk_tags=risk_tags,
            missing_fields=missing_fields,
        )
        context = MarketStructureContext(
            status=combined_status,
            market=normalized_market,
            trade_date=trade_date_text,
            market_theme_context=market_theme_context,
            stock_market_position=stock_position,
        )
        return dump_market_structure_model(context)

    def _build_related_boards(
        self,
        boards: Any,
        *,
        sector_rankings: Dict[str, Any],
        concept_rankings: Dict[str, Any],
    ) -> List[StockBoardPosition]:
        if not isinstance(boards, list):
            return []

        related: List[StockBoardPosition] = []
        for board in boards:
            if not isinstance(board, dict):
                continue
            name = self._optional_text(board.get("name"))
            if not name:
                continue
            board_type = self._optional_text(board.get("type"))
            source, ranking_item = self._resolve_board_rank_source(
                name,
                board_type=board_type,
                sector_rankings=sector_rankings,
                concept_rankings=concept_rankings,
            )
            related.append(
                StockBoardPosition(
                    name=name,
                    type=board_type,
                    code=self._optional_text(board.get("code")),
                    rank=self._safe_int((ranking_item or {}).get("rank")),
                    change_pct=self._safe_float((ranking_item or {}).get("change_pct")),
                    source=source,
                )
            )
        return related

    def _resolve_board_rank_source(
        self,
        name: str,
        *,
        board_type: Optional[str],
        sector_rankings: Dict[str, Any],
        concept_rankings: Dict[str, Any],
    ) -> tuple[ThemeRankSource, Optional[Dict[str, Any]]]:
        if board_type is not None:
            source: ThemeRankSource = "concept" if self._is_concept_type(board_type) else "industry"
            ranking_payload = concept_rankings if source == "concept" else sector_rankings
            return source, self._find_ranking_item(name, ranking_payload)

        concept_item = self._find_ranking_item(name, concept_rankings)
        if concept_item is not None:
            return "concept", concept_item

        sector_item = self._find_ranking_item(name, sector_rankings)
        if sector_item is not None:
            return "industry", sector_item

        if self._is_concept_type(name):
            return "concept", None
        return "industry", None

    def _infer_primary_theme(
        self,
        market_theme_payload: Dict[str, Any],
        related_boards: List[StockBoardPosition],
    ) -> Optional[PrimaryTheme]:
        if not related_boards:
            return None

        related_names = {board.name for board in related_boards}
        candidates: List[Dict[str, Any]] = []
        for field in ("active_themes", "leading_concepts", "leading_industries"):
            value = market_theme_payload.get(field)
            if isinstance(value, list):
                candidates.extend(item for item in value if isinstance(item, dict))

        for item in candidates:
            name = self._optional_text(item.get("name"))
            if not name or name not in related_names:
                continue
            source = self._theme_source(item.get("source"))
            phase = self._theme_phase(item.get("phase"))
            if phase == "unknown":
                phase = self._phase_from_change(self._safe_float(item.get("change_pct")))
            return PrimaryTheme(
                name=name,
                source=source,
                phase=phase,
                rank=self._safe_int(item.get("rank")),
                change_pct=self._safe_float(item.get("change_pct")),
            )

        first = self._select_ranked_related_board(related_boards)
        return PrimaryTheme(
            name=first.name,
            source=first.source,
            phase=self._phase_from_change(first.change_pct),
            rank=first.rank,
            change_pct=first.change_pct,
        )

    @staticmethod
    def _select_ranked_related_board(
        related_boards: List[StockBoardPosition],
    ) -> StockBoardPosition:
        for board in related_boards:
            if board.rank is not None or board.change_pct is not None:
                return board
        return related_boards[0]

    @staticmethod
    def _infer_stock_role(
        primary_theme: Optional[PrimaryTheme],
        related_boards: List[StockBoardPosition],
    ) -> str:
        if primary_theme is None:
            return "edge" if related_boards else "unknown"
        for board in related_boards:
            if board.name == primary_theme.name:
                return "follower"
        return "edge" if related_boards else "unknown"

    @staticmethod
    def _has_primary_market_evidence(primary_theme: Optional[PrimaryTheme]) -> bool:
        if primary_theme is None:
            return False
        return (
            primary_theme.rank is not None
            or primary_theme.change_pct is not None
            or primary_theme.phase != "unknown"
        )

    @staticmethod
    def _resolve_trade_date(
        trade_date: Any,
        market_phase_summary: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if trade_date is not None:
            if isinstance(trade_date, date):
                return trade_date.isoformat()
            text = str(trade_date).strip()
            if text:
                return text
        if isinstance(market_phase_summary, dict):
            for key in ("effective_daily_bar_date", "trade_date", "market_date"):
                value = market_phase_summary.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def _find_ranking_item(name: str, rankings: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(rankings, dict):
            return None
        for field in ("top", "bottom"):
            items = rankings.get(field)
            if not isinstance(items, list):
                continue
            for item in items:
                if isinstance(item, dict) and str(item.get("name") or "").strip() == name:
                    return item
        return None

    @staticmethod
    def _is_concept_type(value: Optional[str]) -> bool:
        text = str(value or "").strip().lower()
        return any(keyword in text for keyword in ("概念", "题材", "concept", "theme"))

    @staticmethod
    def _theme_source(value: Any) -> ThemeRankSource:
        text = str(value or "unknown").strip()
        return text if text in _VALID_THEME_SOURCES else "unknown"

    @staticmethod
    def _theme_phase(value: Any) -> ThemePhase:
        text = str(value or "unknown").strip()
        return text if text in _VALID_THEME_PHASES else "unknown"

    @staticmethod
    def _phase_from_change(value: Optional[float]) -> ThemePhase:
        if value is None:
            return "unknown"
        if value >= 3:
            return "accelerating"
        if value > 0:
            return "warming"
        return "cooling"

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return None
                if text.endswith("%"):
                    text = text[:-1].strip()
                return float(text)
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


__all__ = [
    "MARKET_THEME_SCHEMA_VERSION",
    "MARKET_STRUCTURE_SCHEMA_VERSION",
    "STOCK_MARKET_POSITION_SCHEMA_VERSION",
    "MarketStructureService",
]
