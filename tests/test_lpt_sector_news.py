from src.services.lpt_sector_news import _parse_articles, classify_sector


def test_classifies_required_sectors_and_filters_shopping():
    assert classify_sector("国产人形机器人完成新测试") == "机器人与具身智能"
    assert classify_sector("New satellite enters low Earth orbit") == "卫星与商业航天"
    assert classify_sector("AI model improves semiconductor design") == "科技与芯片"
    assert classify_sector("蓝牙耳机优惠券今日领取") == ""
    assert classify_sector("PS5宇宙机器人游戏首次在PC启动") == ""
    assert classify_sector("米家智能浴霸P1人感版发布") == ""


def test_classification_ignores_unrelated_summary_widgets():
    assert classify_sector("普通消费产品发布", "defense technology robot satellite") == ""


def test_requires_provenance_fields():
    payload = {
        "articles": [
            {
                "title": "Robotics platform enters clinical testing",
                "url": "https://example.com/article",
                "domain": "example.com",
                "seendate": "20260715T010000Z",
            },
            {
                "title": "Satellite launch without original link",
                "url": "",
                "domain": "example.com",
                "seendate": "20260715T010000Z",
            },
        ]
    }
    items = _parse_articles(payload, "国际")
    assert len(items) == 1
    assert all(item["source"] and item["published_at"] and item["url"] for item in items)
