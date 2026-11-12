"""规则引擎回归测试。

样本A 来自 2026-11-12 EIP 录屏还原（26/35kV 交流成盘局放+耐压，合格）：
全部启用的规则应通过。
样本B 构造缺陷（平台只维持6秒 / 稳态局放30pC / 缺耐压步骤）：应命中
R-PD-01、R-PD-03、R-PD-05(或02缺数据视构造)、R-PD-06。
运行：python -m pytest tests/ -v  （或 python tests/test_rules.py）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules.engine import run_all  # noqa: E402

TRIAL = {"voltage_class": "26/35"}

STEPS_GOOD = [
    ("2025-08-19 19:10:51", "试验采集开始"),
    ("2025-08-19 19:10:54", "开始采集局放背景值（请准备完毕后点击采集背景值按钮）"),
    ("2025-08-19 19:10:56", "局放背景值采集完成"),
    ("2025-08-19 19:10:59", "开始进行局放试验请升压到2倍U0-52kV"),
    ("2025-08-19 19:11:19", "当前电压已达到52kV请维持10秒"),
    ("2025-08-19 19:11:29", "当前电压已维持10秒请降压到1.73倍U0-44.98kV"),
    ("2025-08-19 19:11:34", "当前电压已降至44.98kV请点击采集局放值按钮"),
    ("2025-08-19 19:12:11", "局放值采集完成"),
    ("2025-08-19 19:12:14", "开始进行耐压试验请升压到4.5倍U0-117kV"),
    ("2025-08-19 19:14:12", "试验状态：完成"),
]

CURVE_GOOD = [
    {"ts": "2025-08-19 19:11:00", "kv": 26.0, "pc": 3.08},
    {"ts": "2025-08-19 19:11:05", "kv": 38.5, "pc": 3.08},
    {"ts": "2025-08-19 19:11:10", "kv": 46.2, "pc": 3.09},
    {"ts": "2025-08-19 19:11:16", "kv": 50.83, "pc": 3.08},
    {"ts": "2025-08-19 19:11:20", "kv": 52.0, "pc": 3.08},
    {"ts": "2025-08-19 19:11:23", "kv": 52.0, "pc": 3.09},
    {"ts": "2025-08-19 19:11:26", "kv": 51.9, "pc": 3.08},
    {"ts": "2025-08-19 19:11:29", "kv": 52.0, "pc": 3.08},
    {"ts": "2025-08-19 19:11:34", "kv": 44.98, "pc": 3.07},
    {"ts": "2025-08-19 19:11:50", "kv": 44.98, "pc": 3.08},
    {"ts": "2025-08-19 19:13:00", "kv": 80.0, "pc": 3.10},
    {"ts": "2025-08-19 19:13:40", "kv": 100.2, "pc": 3.12},
    {"ts": "2025-08-19 19:14:00", "kv": 116.7, "pc": 3.15},
]


def test_good_sample_all_pass():
    results = run_all(TRIAL, STEPS_GOOD, CURVE_GOOD)
    failed = [r["rule_id"] for r in results if not r["ok"]]
    assert not failed, f"合格样本被误判: {failed}"
    assert len(results) >= 7  # 01,02,03,04,05,06,07,08 至少七条启用


def test_hold_duration_violation():
    steps = STEPS_GOOD
    curve = [
        {"ts": "2025-08-19 19:11:00", "kv": 30.0, "pc": 3.08},
        {"ts": "2025-08-19 19:11:02", "kv": 52.0, "pc": 3.08},
        {"ts": "2025-08-19 19:11:05", "kv": 52.0, "pc": 3.08},
        {"ts": "2025-08-19 19:11:08", "kv": 52.0, "pc": 3.08},  # 平台仅约6秒
        {"ts": "2025-08-19 19:11:20", "kv": 44.98, "pc": 3.08},
        {"ts": "2025-08-19 19:12:00", "kv": 116.7, "pc": 3.08},
    ]
    res = {r["rule_id"]: r for r in run_all(TRIAL, steps, curve)}
    assert res["R-PD-03"]["ok"] is False
    assert res["R-PD-03"]["detail"]["duration_s"] == 6


def test_pd_over_limit():
    curve = [dict(p) for p in CURVE_GOOD]
    for p in curve:
        p["pc"] = 30.0
    res = {r["rule_id"]: r for r in run_all(TRIAL, STEPS_GOOD, curve)}
    assert res["R-PD-06"]["ok"] is False


def test_missing_withstand_step_and_voltage():
    steps = [s for s in STEPS_GOOD if "耐压试验" not in s[1]]
    curve = [p for p in CURVE_GOOD if p["kv"] < 100]  # 未升到耐压
    res = {r["rule_id"]: r for r in run_all(TRIAL, steps, curve)}
    assert res["R-PD-01"]["ok"] is False
    assert res["R-PD-05"]["ok"] is False


def test_stuck_curve_detected():
    from datetime import datetime, timedelta
    base = datetime(2025, 8, 19, 19, 10, 0)
    curve = [dict(p) for p in CURVE_GOOD]
    for i, p in enumerate(curve):
        p["kv"] = 50.0  # 全程同一读数：数据死点
        p["ts"] = (base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
    res = {r["rule_id"]: r for r in run_all(TRIAL, STEPS_GOOD, curve)}
    assert res["R-PD-08"]["ok"] is False


if __name__ == "__main__":
    for fn in (test_good_sample_all_pass, test_hold_duration_violation,
               test_pd_over_limit, test_missing_withstand_step_and_voltage,
               test_stuck_curve_detected):
        fn()
        print("PASS", fn.__name__)
    print("全部通过")
