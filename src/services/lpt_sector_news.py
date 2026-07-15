"""Verified hard-tech news feed for the LPT stock radar page.

Only items with a title, publication time, named source and an original HTTP
link are returned.  This module never infers whether a news item caused a
market move; it simply provides traceable evidence for human review.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, Iterable, List, Tuple
from urllib.parse import urlparse

import requests


GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
HEADERS = {"User-Agent": "Mozilla/5.0 daily-stock-analysis/1.0"}

CATEGORY_TERMS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("卫星与商业航天", ("卫星", "商业航天", "航空航天", "航天", "火箭", "北斗", "低轨", "遥感", "飞船", "月球", "火星", "satellite", "spacecraft", "space launch", "rocket", "orbit", "lunar", "mars")),
    ("机器人与具身智能", ("机器人", "具身智能", "人形机器人", "工业机器人", "机械臂", "robot", "robotics", "humanoid", "embodied intelligence", "automation")),
    ("军工与国防科技", ("军工", "国防科技", "防务", "无人机", "雷达", "导弹", "战斗机", "航空工业", "defense technology", "defence technology", "military technology", "defense", "defence", "missile", "drone")),
    ("科技与芯片", ("人工智能", "大模型", "芯片", "半导体", "算力", "英伟达", "ai model", "artificial intelligence", "semiconductor", "chip", "gpu", "computing power", "large language model", "openai", "deepmind", "anthropic", "nvidia", "tsmc", "asml")),
)

BLOCK_TERMS = (
    "优惠券", "促销", "打折", "购买指南", "游戏", "电影预告", "耳机推荐", "键盘推荐", "浴霸", "美食测评",
    "coupon", "shopping guide", "best deals", "game", "movie trailer", "headphone deal",
)

# These feeds were already used by the user's CGTN Tech Desk Radar.  They are
# the resilient primary layer here; GDELT remains a broader secondary layer.
RSS_SOURCES: Tuple[Dict[str, Any], ...] = (
    {"name": "IT之家", "region": "国内", "url": "https://www.ithome.com/rss/", "weight": 8},
    {"name": "快科技-综合", "region": "国内", "url": "https://rss.mydrivers.com/Rss.aspx?Tid=1", "weight": 5},
    {"name": "快科技-硬件", "region": "国内", "url": "https://rss.mydrivers.com/Rss.aspx?cid=9", "weight": 6},
    {"name": "快科技-科学", "region": "国内", "url": "https://rss.mydrivers.com/Rss.aspx?cid=192", "weight": 7},
    {"name": "36氪", "region": "国内", "url": "https://36kr.com/feed", "weight": 7},
    {"name": "TechNode", "region": "国际", "url": "https://technode.com/feed/", "weight": 8},
    {"name": "TechCrunch", "region": "国际", "url": "https://techcrunch.com/feed/", "weight": 8},
    {"name": "WIRED Science", "region": "国际", "url": "https://www.wired.com/feed/category/science/latest/rss", "weight": 10},
    {"name": "MIT Technology Review", "region": "国际", "url": "https://www.technologyreview.com/feed/", "weight": 12},
    {"name": "Ars Technica", "region": "国际", "url": "https://feeds.arstechnica.com/arstechnica/index", "weight": 9},
    {"name": "CNBC Technology", "region": "国际", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "weight": 9},
    {"name": "SpaceNews", "region": "国际", "url": "https://spacenews.com/feed/", "weight": 13},
    {"name": "NASA News", "region": "国际", "url": "https://www.nasa.gov/news-release/feed/", "weight": 14},
    {"name": "Defense News", "region": "国际", "url": "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", "weight": 13},
    {"name": "Science News", "region": "国际", "url": "https://www.sciencenews.org/feed", "weight": 11},
)


def classify_sector(title: str, description: str = "") -> str:
    # Classification is deliberately title-led. Feed summaries frequently
    # contain unrelated recommendation widgets and previously polluted the
    # satellite/robotics/defense buckets.
    haystack = title.lower()
    if any(term.lower() in haystack for term in BLOCK_TERMS):
        return ""
    for category, terms in CATEGORY_TERMS:
        if any(term.lower() in haystack for term in terms):
            return category
    if re.search(r"(?<![a-z])ai(?![a-z])", haystack):
        return "科技与芯片"
    return ""


def _valid_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=timezone.utc)
            return parsed.astimezone().isoformat(timespec="minutes")
        except ValueError:
            continue
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().isoformat(timespec="minutes")
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"(20\d{2})[-/]?(\d{2})[-/]?(\d{2})", text)
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def _strip_markup(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(node: ET.Element, names: Tuple[str, ...]) -> str:
    for child in node.iter():
        if _local_name(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_link(node: ET.Element) -> str:
    for child in node.iter():
        if _local_name(child.tag) != "link":
            continue
        href = (child.attrib.get("href") or "").strip()
        if _valid_url(href):
            return href
        text = (child.text or "").strip()
        if _valid_url(text):
            return text
    return ""


def _is_recent(iso_value: str, days: int = 7) -> bool:
    if not iso_value:
        return False
    try:
        parsed = datetime.fromisoformat(iso_value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed >= datetime.now(timezone.utc) - timedelta(days=days)
    except ValueError:
        return False


def _parse_rss(content: bytes, source: Dict[str, Any]) -> List[Dict[str, Any]]:
    root = ET.fromstring(content)
    entries = [node for node in root.iter() if _local_name(node.tag) in {"item", "entry"}]
    output: List[Dict[str, Any]] = []
    for entry in entries[:100]:
        title = _strip_markup(_first_text(entry, ("title",)))
        summary = _strip_markup(_first_text(entry, ("description", "summary", "content", "encoded")))
        published_at = _normalize_date(_first_text(entry, ("pubdate", "published", "updated", "date")))
        url = _entry_link(entry)
        category = classify_sector(title, summary)
        if not (title and category and _is_recent(published_at) and _valid_url(url)):
            continue
        output.append({
            "title": title,
            "published_at": published_at,
            "source": source["name"],
            "url": url,
            "category": category,
            "region": source["region"],
            "source_weight": int(source.get("weight", 6)),
        })
    return output


def _fetch_rss_source(source: Dict[str, Any], timeout: int) -> List[Dict[str, Any]]:
    response = requests.get(source["url"], headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return _parse_rss(response.content, source)


def _clean_source(article: Dict[str, Any], url: str) -> str:
    source = str(article.get("domain") or article.get("source") or "").strip()
    if source:
        return source
    return urlparse(url).netloc.removeprefix("www.")


def _parse_articles(payload: Dict[str, Any], region: str) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    for article in payload.get("articles") or []:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        description = str(article.get("seendate") or article.get("socialimage") or "")
        url = str(article.get("url") or "").strip()
        published_at = _normalize_date(article.get("seendate") or article.get("published_at"))
        source = _clean_source(article, url)
        category = classify_sector(title, description)
        if not (title and category and source and published_at and _valid_url(url)):
            continue
        output.append({
            "title": title,
            "published_at": published_at,
            "source": source,
            "url": url,
            "category": category,
            "region": region,
            "source_weight": 6,
        })
    return output


def _gdelt_query(query: str, *, region: str, max_records: int, timeout: int) -> List[Dict[str, Any]]:
    response = requests.get(
        GDELT_URL,
        params={
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max_records,
            "sort": "datedesc",
            "timespan": "3d",
        },
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return _parse_articles(response.json() or {}, region)


def _dedupe(items: Iterable[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    seen = set()
    unique = []
    for item in sorted(
        items,
        key=lambda row: (int(row.get("source_weight", 0)), row.get("published_at", "")),
        reverse=True,
    ):
        key = re.sub(r"\W+", "", item["title"].lower())[:120] or item["url"]
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)

    # Keep the front page useful across all four requested research themes.
    # A second pass fills any unused slots when one theme has fewer stories.
    selected = []
    per_category = max(1, limit // len(CATEGORY_TERMS))
    category_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for item in unique:
        category = item["category"]
        if category_counts.get(category, 0) >= per_category:
            continue
        source = item["source"]
        if source_counts.get(source, 0) >= 4:
            continue
        selected.append(item)
        category_counts[category] = category_counts.get(category, 0) + 1
        source_counts[source] = source_counts.get(source, 0) + 1
    selected_urls = {item["url"] for item in selected}
    for item in unique:
        if len(selected) >= limit:
            break
        if item["url"] not in selected_urls:
            selected.append(item)
            selected_urls.add(item["url"])
    return selected


def fetch_verified_sector_news(max_items: int = 24, timeout: int = 15) -> Dict[str, Any]:
    """Fetch domestic and international hard-tech evidence with provenance."""
    queries = (
        (
            '(卫星 OR 航天 OR 北斗 OR 机器人 OR 具身智能 OR 芯片 OR 半导体 OR 人工智能 OR 军工 OR 国防科技) sourcelang:Chinese',
            "国内",
        ),
        (
            '(satellite OR spaceflight OR robotics OR humanoid OR semiconductor OR "artificial intelligence" OR "defense technology") sourcelang:English',
            "国际",
        ),
    )
    all_items: List[Dict[str, Any]] = []
    errors: List[str] = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(_fetch_rss_source, source, min(timeout, 12)): source
            for source in RSS_SOURCES
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                all_items.extend(future.result())
            except Exception as exc:
                errors.append(f"{source['name']}: {exc}")
    for index, (query, region) in enumerate(queries):
        if index:
            time.sleep(5.2)  # GDELT public API asks clients to avoid rapid calls.
        try:
            all_items.extend(
                _gdelt_query(query, region=region, max_records=max(20, max_items), timeout=timeout)
            )
        except Exception as exc:
            errors.append(f"{region}科技新闻: {exc}")
    return {
        "items": _dedupe(all_items, max_items),
        "errors": errors,
        "policy": "仅展示含标题、发布时间、来源和原文链接的新闻；不据此自动推断股价因果。",
    }
