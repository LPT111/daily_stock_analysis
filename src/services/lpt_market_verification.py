"""Conservative dual-source validation for A-share quote and valuation fields.

The verifier intentionally fails closed: a metric is marked ``待核实`` unless
Tencent and Eastmoney return sufficiently close values.  It is supplemental
evidence for the LLM and must never become a single point of failure.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import requests


HEADERS = {
    "User-Agent": "Mozilla/5.0 daily-stock-analysis/1.0",
    "Accept": "*/*",
}


def _number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _scaled(value: Any, divisor: float) -> Optional[float]:
    parsed = _number(value)
    return parsed / divisor if parsed is not None else None


def _market_code(code: str) -> tuple[str, str]:
    digits = "".join(ch for ch in str(code or "") if ch.isdigit())[-6:]
    if len(digits) != 6:
        raise ValueError(f"Unsupported A-share code: {code}")
    is_sh = digits.startswith(("5", "6", "9"))
    return digits, ("sh" if is_sh else "sz")


def fetch_tencent_quote(code: str, timeout: int = 8) -> Dict[str, Any]:
    digits, prefix = _market_code(code)
    response = requests.get(
        f"https://qt.gtimg.cn/q={prefix}{digits}",
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    payload = response.text.split('="', 1)[-1].rsplit('"', 1)[0].split("~")
    if len(payload) < 53:
        raise ValueError("Tencent quote payload is incomplete")
    return {
        "source": "腾讯财经",
        "price": _number(payload[3]),
        "previous_close": _number(payload[4]),
        "change_pct": _number(payload[32]),
        # Tencent field 52 tracks dynamic PE for the current endpoint.  Field
        # 39 is a different valuation field and previously caused a 10x error.
        "dynamic_pe": _number(payload[52]),
        "pb": _number(payload[46]),
        "source_url": f"https://gu.qq.com/{prefix}{digits}",
    }


def fetch_eastmoney_quote(code: str, timeout: int = 8) -> Dict[str, Any]:
    digits, prefix = _market_code(code)
    secid = f"{1 if prefix == 'sh' else 0}.{digits}"
    response = requests.get(
        "https://push2.eastmoney.com/api/qt/stock/get",
        params={"secid": secid, "fields": "f43,f57,f58,f60,f162,f167,f170"},
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    data = (response.json() or {}).get("data") or {}
    if not data:
        raise ValueError("Eastmoney quote payload is empty")
    return {
        "source": "东方财富",
        "price": _scaled(data.get("f43"), 100),
        "previous_close": _scaled(data.get("f60"), 100),
        "change_pct": _scaled(data.get("f170"), 100),
        # Eastmoney f162 and f167 are both returned with two implied decimal
        # places on this endpoint.  Keeping f162 unscaled previously produced
        # a false 100x PE mismatch.
        "dynamic_pe": _scaled(data.get("f162"), 100),
        "pb": _scaled(data.get("f167"), 100),
        "source_url": f"https://quote.eastmoney.com/{prefix}{digits}.html",
    }


def _compare(
    left: Optional[float],
    right: Optional[float],
    *,
    relative_tolerance: float,
    absolute_tolerance: float = 0.0,
) -> bool:
    if left is None or right is None:
        return False
    if left == 0 and right == 0:
        return True
    if left * right < 0:
        return False
    scale = max(abs(left), abs(right), 1e-9)
    return abs(left - right) <= max(absolute_tolerance, relative_tolerance * scale)


def _metric(
    key: str,
    tencent: Optional[Dict[str, Any]],
    eastmoney: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    left = tencent.get(key) if tencent else None
    right = eastmoney.get(key) if eastmoney else None
    if key == "price":
        confirmed = _compare(left, right, relative_tolerance=0.005, absolute_tolerance=0.02)
    elif key == "change_pct":
        confirmed = _compare(left, right, relative_tolerance=0.02, absolute_tolerance=0.15)
    else:
        confirmed = _compare(left, right, relative_tolerance=0.10, absolute_tolerance=0.05)

    value = (left + right) / 2 if confirmed and left is not None and right is not None else None
    display = "待核实"
    interpretation = "两个数据源未同时返回一致结果，不得用于决策。"
    if confirmed and value is not None:
        display = f"{value:.2f}"
        interpretation = "腾讯财经与东方财富交叉核验一致。"
        if key == "dynamic_pe" and value <= 0:
            display = f"{value:.2f}（亏损状态）"
            interpretation = "动态市盈率为负，表示当前口径下盈利为负，不能按正 PE 倍数进行估值判断。"
    return {
        "status": "confirmed" if confirmed else "待核实",
        "value": value,
        "display": display,
        "interpretation": interpretation,
        "sources": {
            "腾讯财经": left,
            "东方财富": right,
        },
    }


def verify_a_share_quote(code: str) -> Dict[str, Any]:
    """Return a serializable dual-source validation package."""
    errors = []
    tencent: Optional[Dict[str, Any]] = None
    eastmoney: Optional[Dict[str, Any]] = None
    try:
        tencent = fetch_tencent_quote(code)
    except Exception as exc:  # network sources must remain fail-open
        errors.append(f"腾讯财经: {exc}")
    try:
        eastmoney = fetch_eastmoney_quote(code)
    except Exception as exc:
        errors.append(f"东方财富: {exc}")

    metrics = {
        key: _metric(key, tencent, eastmoney)
        for key in ("price", "change_pct", "dynamic_pe", "pb")
    }
    return {
        "code": _market_code(code)[0],
        "policy": "仅 status=confirmed 的字段可用于结论；待核实字段必须忽略。",
        "metrics": metrics,
        "raw_sources": [item for item in (tencent, eastmoney) if item],
        "errors": errors,
    }
