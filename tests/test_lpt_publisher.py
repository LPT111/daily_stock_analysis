from scripts.publish_lpt_stock_radar import build_digest, render_page


def _validation():
    return {
        "code": "600118",
        "metrics": {
            "price": {"status": "confirmed", "display": "73.92", "sources": {"腾讯财经": 73.92, "东方财富": 73.92}},
            "change_pct": {"status": "confirmed", "display": "-10.00", "sources": {"腾讯财经": -10.0, "东方财富": -10.0}},
            "dynamic_pe": {"status": "confirmed", "display": "-511.88（亏损状态）", "sources": {"腾讯财经": -511.88, "东方财富": -511.88}},
            "pb": {"status": "待核实", "display": "待核实", "sources": {"腾讯财经": 13.82, "东方财富": None}},
        },
    }


def test_page_and_digest_are_mobile_summary_first():
    news = [{
        "title": "卫星产业新进展",
        "published_at": "2026-07-15T09:00+08:00",
        "source": "example.com",
        "url": "https://example.com/news",
        "category": "卫星与商业航天",
        "region": "国内",
    }]
    page = render_page(
        stock_report="# 核心结论\n持有",
        market_report="# 大盘\n震荡观察。",
        validations=[_validation()],
        news_package={"items": news, "errors": []},
        generated_at="2026-07-15T15:00:00+08:00",
    )
    assert "近 3 个月 K 线" in page
    assert "mobile-nav" in page
    assert "https://example.com/news" in page
    digest = build_digest(
        stock_report="""核心结论：持有
理想买入点 | 70.00-72.00 元
仓位建议：2成以内
止损位：68.50 元
触发失效条件：放量跌破68.50元且两个交易日未收回
""",
        market_report="市场震荡，等待确认。",
        validations=[_validation()],
        news_items=news,
        dashboard_url="https://example.com/",
    )
    assert "持有" in digest
    assert "买入区间 70.00-72.00 元" in digest
    assert "仓位 2成以内" in digest
    assert "止损 68.50 元" in digest
    assert "失效条件 放量跌破68.50元" in digest
    assert "网页版完整报告" in digest
    assert len(digest) <= 1500
