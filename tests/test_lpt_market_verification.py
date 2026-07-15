from src.services.lpt_market_verification import _metric


def test_metric_confirms_close_values():
    result = _metric("price", {"price": 73.92}, {"price": 73.91})
    assert result["status"] == "confirmed"
    assert result["display"] != "待核实"


def test_metric_rejects_single_source():
    result = _metric("dynamic_pe", {"dynamic_pe": 5149.0}, None)
    assert result["status"] == "待核实"
    assert result["value"] is None


def test_negative_pe_is_not_described_as_high_valuation():
    result = _metric("dynamic_pe", {"dynamic_pe": -511.9}, {"dynamic_pe": -511.8})
    assert result["status"] == "confirmed"
    assert "亏损状态" in result["display"]
    assert "高估" not in result["interpretation"]
