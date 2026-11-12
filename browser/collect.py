"""T1 采集编排器：驱动浏览器走完 工单列表 → 试验详情，
接口拦截的数据实时进 ApiAdapter，最后入库、跑规则、出报表。

用法见 run_collect.py。真实 EIP 上线时只改 config/api_map.yaml。
"""
import time
from datetime import datetime
from pathlib import Path

from browser.adapter import ApiAdapter
from browser.ctx import BrowserSession
from browser.intercept import ResponseSink, attach
from reports.excel_report import write_anomaly_excel
from reports.html_report import write_anomaly_html
from rules.engine import run_all
from storage.db import (connect, create_task, insert_anomaly,
                        insert_trial_with_steps_and_curve, insert_workorder)

ROOT = Path(__file__).resolve().parents[1]


def collect(base_url: str, limit: int = None, headless: bool = True,
            page_wait_ms: int = 1200, task_note: str = "") -> dict:
    """返回摘要 {workorders, trials, anomalies, excel, html}。"""
    out_dir = ROOT / "evidence" / f"collect_{datetime.now():%Y%m%d_%H%M%S}"
    sink = ResponseSink(out_dir)
    adapter = ApiAdapter()

    with BrowserSession(headless=headless, record_video=True,
                        profile_dir=out_dir / "profile") as s:
        page = s.page
        detach = attach(page, sink)

        # 1) 列表页：触发 /api/workorders
        page.goto(base_url.rstrip("/") + "/", wait_until="networkidle")
        time.sleep(page_wait_ms / 1000)

        # 2) 先重放列表接口，拿到详情页目标
        adapter.replay_manifest(out_dir / "manifest.jsonl")
        targets = [w["detail_url"] for w in adapter.staged_workorders if w.get("detail_url")]
        if limit:
            targets = targets[:limit]

        # 3) 逐个进入详情页：触发 /api/trials/<key> 与 /curve
        for rel in targets:
            page.goto(base_url.rstrip("/") + rel, wait_until="networkidle")
            time.sleep(page_wait_ms / 1000)
        detach()

    # 4) 全部落盘后统一重放解析（确定性路径，不受事件时序影响）
    adapter.replay_manifest(out_dir / "manifest.jsonl")

    # 3) 入库 + 规则 + 报表
    conn = connect(ROOT / "eip_agent.db")
    task_id = create_task(conn, "collect", {"base_url": base_url},
                          note=task_note or f"T1采集 {base_url}")
    for w in adapter.staged_workorders:
        insert_workorder(conn, task_id, w)

    rows, n_anomaly = [], 0
    for key, slot in adapter.complete_trials():
        trial, steps, curve = slot["trial"], slot["steps"], slot["curve"]
        trial_id = insert_trial_with_steps_and_curve(
            conn, task_id, trial, steps,
            [(p["ts"], p.get("kv"), p.get("pc"), "api", None, "") for p in curve],
        )
        for r in run_all(trial, steps, curve):
            insert_anomaly(conn, trial_id, r, evidence_path=str(out_dir))
            rows.append({"trial_id": trial_id, "ok": r["ok"], "detail": r["detail"],
                         "review_status": "待复核", **trial, **r})
            n_anomaly += 0 if r["ok"] else 1
    conn.close()

    report_dir = ROOT / "reports" / "out"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    xlsx = write_anomaly_excel(rows, report_dir / f"异常报表_T1采集_{stamp}.xlsx")
    html = write_anomaly_html(rows, report_dir / f"异常报表_T1采集_{stamp}.html")
    return {"workorders": len(adapter.staged_workorders),
            "trials": len(list(adapter.complete_trials())),
            "anomalies": n_anomaly, "rows": rows,
            "excel": str(xlsx), "html": str(html), "evidence": str(out_dir),
            "unmatched": adapter.unmatched[:10], "intercept_errors": sink.errors[:10]}
