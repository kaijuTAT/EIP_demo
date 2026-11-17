"""端到端演示：不连 EIP，用参照录屏构造的脱敏样本跑通
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
from storage.db import connect, create_task, insert_trial_with_steps_and_curve, update_task

from mock_eip.data import BAD_PD_CURVE, BAD_PD_STEPS, GOOD_CURVE, GOOD_STEPS

# 采用与本地仿真相同的业务时轴、脱敏编号及合成测点。
TRIAL_GOOD = {"product_no": "20250819-XX#", "test_no": "TEST-20250819-001",
              "voltage_class": "26/35", "voltage_class_type": "交流", "test_status": "完成"}
TRIAL_BAD = {**TRIAL_GOOD, "product_no": "20250910-XX#", "test_no": "TEST-20250910-007"}
STEPS_GOOD, CURVE_GOOD = GOOD_STEPS, GOOD_CURVE
STEPS_BAD, CURVE_BAD = BAD_PD_STEPS, BAD_PD_CURVE


def run_demo(task_id=None):
    root = Path(__file__).resolve().parent
    conn = connect(root / "eip_agent.db")
    if task_id is None:
        task_id = create_task(conn, "rules_run", {"demo": True}, note="录屏样本回归演示")

    update_task(conn, task_id, "running")
    rows = []
    try:
        with conn:
            for trial, steps, curve in ((TRIAL_GOOD, STEPS_GOOD, CURVE_GOOD),
                                        (TRIAL_BAD, STEPS_BAD, CURVE_BAD)):
                tid = insert_trial_with_steps_and_curve(conn, task_id, trial, steps,
                    [(ts, kv, pc, "demo", 1.0, "") for ts, kv, pc in curve], commit=False)
                points = [{"ts": ts, "kv": kv, "pc": pc} for ts, kv, pc in curve]
                results = run_all(trial, steps, points)
                rows.extend({**trial, **r, "trial_id": tid, "review_status": "待复核"} for r in results)
                flags = [r["rule_id"] + " " + r["verdict"] for r in results if r["ok"] is not True]
                print(f"trial {tid} ({trial['product_no']}) → " + ("、".join(flags) or "全部通过"))
            out = root / "reports" / "out"
            xlsx = write_anomaly_excel(rows, out / "异常报表_样例.xlsx")
            html = write_anomaly_html(rows, out / "异常报表_样例.html")
            summary = {"trials": 2, "anomalies": sum(row["ok"] is False for row in rows),
                       "needs_review": sum(row["ok"] is None for row in rows),
                       "excel": str(xlsx), "html": str(html)}
            update_task(conn, task_id, "done", result=summary, commit=False)
        print(f"报表已生成\n  {xlsx}\n  {html}")
        return summary
    except Exception as exc:
        update_task(conn, task_id, "failed", error=str(exc))
        raise
    finally:
        conn.close()


def main():
    run_demo()


if __name__ == "__main__":
    main()
