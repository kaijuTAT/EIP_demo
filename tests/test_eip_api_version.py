"""T1 接口版端到端集成测试：Mock EIP + 无头浏览器 + 采集 + 规则 + 报表。

覆盖：adapter 字段映射 / 拦截回调 / 入库 / 规则判定 / 报表产出。
运行：python tests/test_eip_api_version.py
"""
import sys
import threading
from pathlib import Path
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PORT = 8799


def _start_mock():
    from mock_eip.server import app
    server = make_server("127.0.0.1", PORT, app)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_adapter_jpath():
    from browser.adapter import jpath
    doc = {"data": {"list": [{"no": "WO-1"}, {"no": "WO-2"}], "total": 2}}
    assert jpath(doc, "$.data.total") == 2
    assert jpath(doc, "$.data.list")[1]["no"] == "WO-2"
    assert jpath(doc, "$.missing.path") is None
    print("PASS test_adapter_jpath")


def test_adapter_feed_curve_endpoints():
    import json
    from browser.adapter import ApiAdapter
    ad = ApiAdapter()
    ad.feed("http://x/api/trials/K1", json.dumps(
        {"data": {"productNo": "P1#", "testNo": "T1", "voltageClass": "26/35",
                  "voltageClassType": "交流", "testStatus": "完成",
                  "steps": [{"time": "2025-08-19 19:10:51", "text": "试验采集开始"}]}}
    ).encode())
    ad.feed("http://x/api/trials/K1/curve", json.dumps(
        {"data": {"points": [{"t": "2025-08-19 19:11:00", "kv": 52.0, "pc": 3.08}]}}
    ).encode())
    assert len(ad.staged_workorders) == 0
    key, slot = list(ad.complete_trials())[0]
    assert key == "K1" and slot["trial"]["product_no"] == "P1#"
    assert slot["steps"] == [("2025-08-19 19:10:51", "试验采集开始")]
    assert slot["curve"][0]["kv"] == 52.0
    print("PASS test_adapter_feed_curve_endpoints")


def test_end_to_end_collect():
    from browser.collect import collect
    server = _start_mock()
    try:
        summary = collect(f"http://127.0.0.1:{PORT}", headless=True, page_wait_ms=800,
                          task_note="集成测试")
    finally:
        server.shutdown()

    assert summary["workorders"] == 3, summary
    assert summary["trials"] == 3, summary
    by_product = {}
    for row in summary["rows"]:
        by_product.setdefault(row["product_no"], {})[row["rule_id"]] = row["ok"]

    xx1 = by_product["20250819-XX1#"]
    assert all(xx1.values()), f"合格样本被误判: {xx1}"
    xx2 = by_product["20250910-XX2#"]
    assert xx2.get("R-PD-03") is False, "平台维持6秒未命中 R-PD-03"
    assert xx2.get("R-PD-06") is False, "稳态局放30pC未命中 R-PD-06"
    xx3 = by_product["20250912-XX3#"]
    assert xx3.get("R-PD-01") is False, "缺耐压步骤未命中 R-PD-01"
    assert xx3.get("R-PD-05") is False, "电压未达117kV未命中 R-PD-05"

    assert Path(summary["excel"]).exists() and Path(summary["html"]).exists()
    print(f"PASS test_end_to_end_collect "
          f"(3工单/3试验/异常{summary['anomalies']}条, 报表已生成)")


if __name__ == "__main__":
    test_adapter_jpath()
    test_adapter_feed_curve_endpoints()
    test_end_to_end_collect()
    print("全部通过")
