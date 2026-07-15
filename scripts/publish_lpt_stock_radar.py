#!/usr/bin/env python3
"""Build the mobile stock radar page and send one digest per push channel."""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import markdown2
import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.lpt_market_verification import verify_a_share_quote
from src.services.lpt_sector_news import fetch_verified_sector_news


DEFAULT_DASHBOARD = "https://LPT111.github.io/daily_stock_analysis/"
ACTION_PATTERN = re.compile(r"(?:核心结论|动作|操作建议)[^\n]{0,40}?(买入|持有|减仓|卖出)")


def _latest(directory: Path, pattern: str) -> Optional[Path]:
    candidates = [path for path in directory.glob(pattern) if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _read(path: Optional[Path]) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path else ""


def _plain_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"!\[[^]]*]\([^)]+\)", "", text)
    text = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`#>|]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _excerpt(text: str, limit: int = 220) -> str:
    plain = _plain_markdown(text)
    return plain[:limit].rstrip("，。； ") + ("…" if len(plain) > limit else "")


def _extract_action(text: str) -> str:
    match = ACTION_PATTERN.search(text)
    if match:
        return match.group(1)
    for action in ("减仓", "卖出", "持有", "买入"):
        if action in text:
            return action
    return "待确认"


def _extract_report_field(text: str, labels: Iterable[str], limit: int = 90) -> str:
    """Read a compact decision field from either prose or a Markdown table row."""
    lines = text.splitlines()
    for index, raw_line in enumerate(lines):
        plain = _plain_markdown(raw_line).strip()
        matched_label = next((label for label in labels if label in plain), None)
        if not matched_label:
            continue

        value = plain.split(matched_label, 1)[1]
        value = re.sub(r"^[\s:：|\-]+", "", value).strip()
        if not value:
            for next_line in lines[index + 1:index + 3]:
                candidate = _plain_markdown(next_line).strip()
                candidate = re.sub(r"^[\s:：|\-]+", "", candidate).strip()
                if candidate and not re.fullmatch(r"[-|:：\s]+", candidate):
                    value = candidate
                    break
        if value:
            return value[:limit].rstrip("，。； ") + ("…" if len(value) > limit else "")
    return "待报告生成"


def _execution_plan(stock_report: str, action: str) -> Dict[str, str]:
    """Extract the model plan and fill every missing field conservatively."""
    plan = {
        "buy_range": _extract_report_field(stock_report, ("理想买入点", "买入区间", "参考买入区间")),
        "position": _extract_report_field(stock_report, ("仓位建议", "建议仓位")),
        "stop_loss": _extract_report_field(stock_report, ("止损位", "止损线", "参考止损")),
        "invalidation": _extract_report_field(stock_report, ("触发失效条件", "失效条件", "判断失效条件"), limit=120),
    }
    ma20_match = re.search(r"MA20[^0-9]{0,12}([0-9]+(?:\.[0-9]+)?)", stock_report, flags=re.I)
    ma20 = ma20_match.group(1) if ma20_match else "MA20"

    fallbacks = {
        "买入": {
            "buy_range": f"等待回踩{ma20}附近止跌后分批，不追高",
            "position": "首次不超过2成，确认后最高4成",
            "stop_loss": f"有效跌破{ma20}且两个交易日未收回",
            "invalidation": "跌破止损位或所属板块同步转弱时，买入逻辑失效",
        },
        "持有": {
            "buy_range": f"仅在{ma20}附近企稳时考虑低吸，不追涨",
            "position": "控制在3成以内",
            "stop_loss": f"有效跌破{ma20}且两个交易日未收回",
            "invalidation": "跌破止损位或量价结构转空时，持有逻辑失效",
        },
        "减仓": {
            "buy_range": f"暂不新增；重新站稳{ma20}后再评估",
            "position": "降至1成以内",
            "stop_loss": "反弹无力或再创近20日新低时继续退出",
            "invalidation": f"未来3个交易日重新站稳{ma20}且量价修复，则减仓判断失效并重新评估",
        },
        "卖出": {
            "buy_range": f"暂不新增；重新站稳{ma20}后再评估",
            "position": "降至0成",
            "stop_loss": "不等待额外确认，按既定纪律退出",
            "invalidation": f"未来3个交易日重新站稳{ma20}且量价修复，则卖出判断失效并重新评估",
        },
    }
    fallback = fallbacks.get(action, fallbacks["持有"])
    for key, value in plan.items():
        if not value or value == "待报告生成":
            plan[key] = fallback[key]
    return plan


def _stock_codes() -> List[str]:
    raw = os.getenv("STOCK_LIST_CONFIG") or os.getenv("STOCK_LIST") or "600118"
    output: List[str] = []
    for token in re.split(r"[,;\s]+", raw):
        digits = "".join(ch for ch in token if ch.isdigit())[-6:]
        if len(digits) == 6 and digits not in output:
            output.append(digits)
    return output or ["600118"]


def _validation_summary(validation: Dict[str, Any]) -> str:
    labels = {"price": "价格", "change_pct": "涨跌幅", "dynamic_pe": "动态PE", "pb": "PB"}
    parts = []
    for key, label in labels.items():
        metric = validation.get("metrics", {}).get(key, {})
        parts.append(f"{label}：{metric.get('display', '待核实')}")
    return "；".join(parts)


def _news_by_category(items: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(item["category"], []).append(item)
    return grouped


def _section_id(label: str) -> str:
    return re.sub(r"\W+", "-", label)


def _render_news(items: List[Dict[str, Any]]) -> str:
    if not items:
        return '<p class="empty">本次未获取到同时具备标题、时间、来源和原文链接的可核验新闻。</p>'
    cards = []
    for item in items:
        cards.append(
            '<article class="news-card">'
            f'<div class="eyebrow">{html.escape(item["category"])} · {html.escape(item["region"])}</div>'
            f'<h3>{html.escape(item["title"])}</h3>'
            f'<p>{html.escape(item["published_at"])} · {html.escape(item["source"])}</p>'
            f'<a href="{html.escape(item["url"], quote=True)}" target="_blank" rel="noopener">查看原文</a>'
            '</article>'
        )
    return "".join(cards)


def _render_validation(validation: Dict[str, Any]) -> str:
    labels = {"price": "当前价格", "change_pct": "当日涨跌幅", "dynamic_pe": "动态 PE", "pb": "市净率 PB"}
    blocks = []
    for key, label in labels.items():
        metric = validation.get("metrics", {}).get(key, {})
        status = metric.get("status", "待核实")
        css = "confirmed" if status == "confirmed" else "pending"
        source_text = " / ".join(
            f"{name}: {value if value is not None else '无数据'}"
            for name, value in metric.get("sources", {}).items()
        )
        blocks.append(
            f'<div class="metric {css}"><span>{html.escape(label)}</span>'
            f'<strong>{html.escape(str(metric.get("display", "待核实")))}</strong>'
            f'<small>{html.escape(source_text)}</small></div>'
        )
    return "".join(blocks)


def render_page(
    *,
    stock_report: str,
    market_report: str,
    validations: List[Dict[str, Any]],
    news_package: Dict[str, Any],
    generated_at: str,
) -> str:
    news_items = news_package.get("items", [])
    grouped = _news_by_category(news_items)
    stock_html = markdown2.markdown(stock_report or "本次未生成个股报告。", extras=["tables", "fenced-code-blocks"])
    market_html = markdown2.markdown(market_report or "本次未生成大盘报告。", extras=["tables", "fenced-code-blocks"])
    sections = []
    for category in ("科技与芯片", "卫星与商业航天", "机器人与具身智能", "军工与国防科技"):
        section_id = _section_id(category)
        sections.append(
            f'<section id="{section_id}"><div class="section-head"><h2>{category}</h2>'
            f'<span>{len(grouped.get(category, []))} 条可核验新闻</span></div>'
            f'<div class="news-grid">{_render_news(grouped.get(category, []))}</div></section>'
        )
    nav_links = "".join(
        f'<a href="#{_section_id(category)}">{category}</a>'
        for category in ("科技与芯片", "卫星与商业航天", "机器人与具身智能", "军工与国防科技")
    )
    mobile_nav_links = "".join(
        f'<a href="#{_section_id(category)}">{short_label}</a>'
        for category, short_label in (
            ("科技与芯片", "科技"),
            ("卫星与商业航天", "卫星"),
            ("机器人与具身智能", "机器人"),
            ("军工与国防科技", "军工"),
        )
    )
    validation_html = "".join(
        f'<div class="validation"><h3>{html.escape(item["code"])} 双源校验</h3>'
        f'<div class="metric-grid">{_render_validation(item)}</div></div>'
        for item in validations
    )
    warnings = news_package.get("errors") or []
    warning_html = (
        f'<div class="warning">有 {len(warnings)} 个新闻入口暂时不可用，系统已自动跳过；其余可核验来源正常展示。</div>'
        if warnings else ""
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Stock Analysis · 保守决策版</title>
<style>
:root{{--ink:#10231e;--green:#087f5b;--mint:#e9f7f1;--line:#d8e5df;--paper:#fff;--soft:#f5f8f6;--amber:#a86500;}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--soft);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif;letter-spacing:0}}
a{{color:var(--green)}}.shell{{display:grid;grid-template-columns:220px minmax(0,1fr);max-width:1500px;margin:auto;min-height:100vh}}aside{{position:sticky;top:0;height:100vh;padding:28px 20px;background:#0b493d;color:white}}aside h2{{font-size:19px;margin:0 0 8px}}aside p{{font-size:12px;color:#cfe4dd;margin:0 0 26px}}aside a{{display:block;color:white;text-decoration:none;padding:10px 0;border-bottom:1px solid rgba(255,255,255,.14);font-size:13px}}main{{padding:28px 34px 70px;min-width:0}}.hero{{background:var(--paper);border:1px solid var(--line);padding:30px;border-top:5px solid var(--green)}}.hero h1{{font-size:34px;margin:0 0 10px}}.hero p{{margin:6px 0;color:#52625d}}.badges{{display:flex;gap:8px;flex-wrap:wrap;margin-top:18px}}.badge{{padding:7px 10px;background:var(--mint);color:var(--green);font-weight:700;font-size:12px}}section{{margin-top:28px;scroll-margin-top:20px}}.section-head{{display:flex;align-items:end;justify-content:space-between;border-bottom:1px solid var(--line);margin-bottom:13px}}.section-head h2{{font-size:22px;margin:0 0 10px}}.section-head span{{font-size:12px;color:#718079;margin-bottom:10px}}.validation{{background:white;border:1px solid var(--line);padding:18px;margin-top:12px}}.validation h3{{margin:0 0 12px}}.metric-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}.metric{{border:1px solid var(--line);padding:12px;min-height:100px}}.metric span,.metric small{{display:block;font-size:11px;color:#718079}}.metric strong{{display:block;font-size:19px;margin:8px 0}}.metric.pending{{border-left:4px solid var(--amber)}}.metric.confirmed{{border-left:4px solid var(--green)}}.news-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.news-card{{background:white;border:1px solid var(--line);padding:16px}}.news-card h3{{font-size:16px;line-height:1.5;margin:7px 0}}.news-card p,.eyebrow{{font-size:12px;color:#718079}}.news-card a{{display:inline-block;margin-top:8px;font-weight:700;text-decoration:none}}details{{background:white;border:1px solid var(--line);margin-top:12px}}summary{{cursor:pointer;padding:16px;font-weight:800;color:var(--green)}}.report{{padding:0 20px 24px;line-height:1.7;overflow:auto}}.report table{{border-collapse:collapse;width:100%;font-size:12px}}.report th,.report td{{border:1px solid var(--line);padding:7px;text-align:left}}.warning,.empty{{background:#fff7e7;color:#7b4d00;padding:12px;font-size:13px}}.mobile-nav{{display:none}}
@media(max-width:800px){{.shell{{display:block}}aside{{display:none}}main{{padding:14px 12px 85px}}.hero{{padding:20px}}.hero h1{{font-size:25px}}.metric-grid,.news-grid{{grid-template-columns:1fr 1fr}}.metric{{min-height:92px}}.mobile-nav{{position:fixed;display:grid;grid-template-columns:repeat(7,minmax(0,1fr));z-index:20;left:10px;right:10px;bottom:10px;background:#0b493d;padding:5px;box-shadow:0 8px 25px #0003}}.mobile-nav a{{display:flex;align-items:center;justify-content:center;color:white;text-decoration:none;white-space:nowrap;font-size:10px;min-height:34px;padding:4px 2px}}}}
@media(max-width:480px){{.metric-grid,.news-grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="shell"><aside><h2>Daily Stock Analysis</h2><p>三个月行情 · 双源校验 · 保守决策</p><a href="#overview">今日总览</a><a href="#verification">数据校验</a>{nav_links}<a href="#reports">完整报告</a></aside>
<main><header class="hero" id="overview"><h1>A股科技主题决策雷达</h1><p>每日覆盖大盘、科技、卫星、机器人与军工板块。</p><p>生成时间：{html.escape(generated_at)} · 更新：北京时间 08:30 / 15:00</p><div class="badges"><span class="badge">近 3 个月 K 线</span><span class="badge">价格/涨跌幅/PE/PB 双源核验</span><span class="badge">新闻必须含时间/来源/原文</span><span class="badge">保守仓位与止损纪律</span></div></header>
<section id="verification"><div class="section-head"><h2>行情与估值校验</h2><span>冲突数据自动标记待核实</span></div>{validation_html}</section>{warning_html}
{''.join(sections)}
<section id="reports"><div class="section-head"><h2>完整分析</h2><span>网页完整版</span></div><details><summary>展开个股决策报告</summary><div class="report">{stock_html}</div></details><details><summary>展开大盘与板块报告</summary><div class="report">{market_html}</div></details></section>
<p style="color:#718079;font-size:12px;margin-top:28px">研究参考，不构成投资建议。模型结论必须服从数据校验和风险纪律。</p></main></div><nav class="mobile-nav"><a href="#overview">总览</a><a href="#verification">校验</a>{mobile_nav_links}<a href="#reports">报告</a></nav></body></html>"""


def _top_news_lines(news_items: List[Dict[str, Any]], limit: int = 4) -> List[str]:
    lines = []
    seen_categories = set()
    for item in news_items:
        if item["category"] in seen_categories and len(lines) < len(set(i["category"] for i in news_items)):
            continue
        seen_categories.add(item["category"])
        lines.append(f"- {item['category']}：{item['title']}（{item['source']}，{item['published_at']}）\n  {item['url']}")
        if len(lines) >= limit:
            break
    return lines


def build_digest(
    *,
    stock_report: str,
    market_report: str,
    validations: List[Dict[str, Any]],
    news_items: List[Dict[str, Any]],
    dashboard_url: str,
) -> str:
    action = _extract_action(stock_report)
    plan = _execution_plan(stock_report, action)
    lines = [
        "【A股科技主题决策雷达】",
        f"个股建议：{action}（基于近3个月K线，保守模式）",
        f"执行参考：买入区间 {plan['buy_range']}｜仓位 {plan['position']}",
        f"风控纪律：止损 {plan['stop_loss']}｜失效条件 {plan['invalidation']}",
        f"大盘摘要：{_excerpt(market_report, 180)}",
    ]
    for item in validations:
        lines.append(f"{item['code']} 双源校验：{_validation_summary(item)}")
    lines.extend(["", "可核验科技新闻："])
    lines.extend(_top_news_lines(news_items) or ["- 本次暂无四要素齐全的可核验新闻。"])
    lines.extend(["", f"网页版完整报告：{dashboard_url}", "仅供研究参考，不构成投资建议。"])
    digest = "\n".join(lines)
    return digest[:1500]


def send_once(digest: str, dashboard_url: str) -> Dict[str, str]:
    results: Dict[str, str] = {}
    pushplus = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if pushplus:
        content = markdown2.markdown(digest.replace(dashboard_url, f"[打开网页版]({dashboard_url})"))
        response = requests.post(
            "https://www.pushplus.plus/send",
            json={"token": pushplus, "title": "A股科技主题决策雷达已更新", "content": content, "template": "html"},
            timeout=15,
        )
        response.raise_for_status()
        results["微信/PushPlus"] = "sent"
    else:
        results["微信/PushPlus"] = "skipped: token missing"

    feishu = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if feishu:
        response = requests.post(
            feishu,
            json={
                "msg_type": "interactive",
                "card": {
                    "header": {"template": "green", "title": {"tag": "plain_text", "content": "A股科技主题决策雷达已更新"}},
                    "elements": [
                        {"tag": "markdown", "content": digest.replace(dashboard_url, "")},
                        {"tag": "action", "actions": [{"tag": "button", "type": "primary", "text": {"tag": "plain_text", "content": "打开网页版"}, "url": dashboard_url}]},
                    ],
                },
            },
            timeout=15,
        )
        response.raise_for_status()
        results["飞书"] = "sent"
    else:
        results["飞书"] = "skipped: webhook missing"
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--docs-dir", type=Path, default=ROOT / "docs")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    stock_report_path = _latest(args.reports_dir, "report_*.md")
    market_report_path = _latest(args.reports_dir, "market_review_*.md")
    stock_report = _read(stock_report_path)
    market_report = _read(market_report_path)
    validations = [verify_a_share_quote(code) for code in _stock_codes()]
    news_package = fetch_verified_sector_news()
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    dashboard_url = os.getenv("PUBLIC_DASHBOARD_URL", DEFAULT_DASHBOARD).strip() or DEFAULT_DASHBOARD

    args.docs_dir.mkdir(parents=True, exist_ok=True)
    page = render_page(
        stock_report=stock_report,
        market_report=market_report,
        validations=validations,
        news_package=news_package,
        generated_at=generated_at,
    )
    (args.docs_dir / "index.html").write_text(page, encoding="utf-8")
    digest = build_digest(
        stock_report=stock_report,
        market_report=market_report,
        validations=validations,
        news_items=news_package.get("items", []),
        dashboard_url=dashboard_url,
    )
    latest = {
        "generated_at": generated_at,
        "dashboard_url": dashboard_url,
        "stock_report": stock_report_path.name if stock_report_path else None,
        "market_report": market_report_path.name if market_report_path else None,
        "validations": validations,
        "sector_news": news_package,
        "digest": digest,
    }
    (args.docs_dir / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.docs_dir / "latest-report.md").write_text(digest + "\n", encoding="utf-8")
    print(f"Dashboard generated: {args.docs_dir / 'index.html'}")
    if not args.no_push:
        print(json.dumps(send_once(digest, dashboard_url), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
