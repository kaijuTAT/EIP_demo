"""端到端演示：不连 EIP，用 2026-11-12 录屏还原的真实样本跑通
入库 → 规则 → Excel/HTML 报表 全流程，让业务立刻看到报表长什么样。

用法：在 eip-agent 目录下  python run_demo.py
产出：reports/out/异常报表_样例.xlsx / .html
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from reports.excel_report import write_anomaly_excel
from reports.html_report import write_anomaly_html
from rules.engine import run_all
from storage.db import connect, create_task, insert_trial_with_steps_and_curve

# ── 样本A：录屏还原的合格试验（2025-08-19 成盘局放+耐压，26/35kV 交流）──
# 编号已脱敏为合成样例
TRIAL_GOOD = {
    "product_no": "20250819-XX#", "test_no": "TEST-20250819-001",
    "voltage_class": "26/35", "voltage_class_type": "交流", "test_status": "完成",
}
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
# 曲线：升压→52kV平台10s→降压44.98→耐压116.7；悬浮框实测 kV:50.83 / pC:3.08
CURVE_GOOD = [
    ("2025-08-19 19:11:00", 26.0, 3.08), ("2025-08-19 19:11:05", 38.5, 3.08),
    ("2025-08-19 19:11:10", 46.2, 3.09), ("2025-08-19 19:11:16", 50.83, 3.08),
    ("2025-08-19 19:11:20", 52.0, 3.08), ("2025-08-19 19:11:23", 52.0, 3.09),
    ("2025-08-19 19:11:26", 51.9, 3.08), ("2025-08-19 19:11:29", 52.0, 3.08),
    ("2025-08-19 19:11:34", 44.98, 3.07), ("2025-08-19 19:11:50", 44.98, 3.08),
    ("2025-08-19 19:13:00", 80.0, 3.10), ("2025-08-19 19:13:40", 100.2, 3.12),
    ("2025-08-19 19:14:00", 116.7, 3.15),
]

# ── 样本B：模拟一条问题试验（平台只维持6秒、稳态局放30pC、缺耐压步骤）──
TRIAL_BAD = {
    "product_no": "20250910-XX#", "test_no": "TEST-20250910-007",
    "voltage_class": "26/35", "voltage_class_type": "交流", "test_status": "完成",
}
STEPS_BAD = [s for s in STEPS_GOOD if "耐压试验" not in s[1] and "完成" not in s[1]]
CURVE_BAD = [
    ("2025-09-10 10:00:00", 26.0, 3.05), ("2025-09-10 10:00:05", 40.0, 5.2),
    ("2025-09-10 10:00:10", 48.0, 18.0), ("2025-09-10 10:00:12", 52.0, 30.0),
    ("2025-09-10 10:00:15", 52.0, 31.0), ("2025-09-10 10:00:18", 52.0, 29.5),
    ("2025-09-10 10:00:25", 44.9, 30.0),
]


def run_demo(task_id=None):
    root = Path(__file__).resolve().parent
    conn = connect(root / "eip_agent.db")
    if task_id is None:
        task_id = create_task(conn, "rules_run", {"demo": True}, note="录屏样本回归演示")

    trials = []
    for trial, steps, curve in ((TRIAL_GOOD, STEPS_GOOD, CURVE_GOOD),
                                (TRIAL_BAD, STEPS_BAD, CURVE_BAD)):
        tid = insert_trial_with_steps_and_curve(
            conn, task_id, trial, steps,
            [(ts, kv, pc, "demo", 1.0, "") for ts, kv, pc in curve],
        )
        trials.append((tid, trial, steps,
                       [{"ts": ts, "kv": kv, "pc": pc} for ts, kv, pc in curve]))

    rows = []
    for tid, trial, steps, curve in trials:
        for r in run_all(trial, steps, curve):
            rows.append({"trial_id": tid, "ok": r["ok"], "detail": r["detail"],
                         "review_status": "待复核", **trial, **r})
        fails = [r["rule_id"] for r in run_all(trial, steps, curve) if not r["ok"]]
        label = "全部通过" if not fails else "异常: " + ",".join(fails)
        print(f"trial {tid} ({trial['product_no']}) → {label}")

    out = root / "reports" / "out"
    xlsx = write_anomaly_excel(rows, out / "异常报表_样例.xlsx")
    html = write_anomaly_html(rows, out / "异常报表_样例.html")
    print(f"\n报表已生成：\n  {xlsx}\n  {html}")
    conn.close()
    return {"trials": len(trials), "anomalies": sum(1 for row in rows if not row["ok"]),
            "excel": str(xlsx), "html": str(html)}


def main():
    run_demo()


if __name__ == "__main__":
    main()
