import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from run_receipts import reconcile, run_receipts
from storage.db import connect


def test_receipt_driven_matching_and_coverage():
    receipts = [{"workorder_no": "A", "product_no": str(i), "received_at": "2025-01-10 12:00:00"}
                for i in range(6)]
    uploads = [{"workorder_no": "A", "product_no": str(i), "uploaded_at": t} for i, t in enumerate(
        ["2025-01-10 12:00:00", "2025-01-11", "2025-01-10", None, "2025-01-06"])]
    uploads.append({**uploads[-1], "uploaded_at": "2025-01-12"})
    assert [r["ok"] for r in reconcile(receipts, uploads)] == [True, False, None, None, None, None]
    rows = reconcile(receipts, uploads, "2025-01-10")
    assert rows[-1]["ok"] is False
    assert reconcile(receipts, uploads, "2025-01-09")[-1]["ok"] is None
    assert reconcile(receipts, [*uploads, {"workorder_no": "A"}], "2025-01-11")[-1]["ok"] is None
    product = {**receipts[0], "batch_no": "lot"}
    other = {**uploads[1], "batch_no": "lot"}
    assert reconcile([product], [other])[0]["ok"] is None


def test_csv_xlsx_and_evidence(tmp_path):
    receipts = tmp_path / "receipts.csv"
    receipts.write_text("工单号,成品编号,收货时间\nA,01,2025-01-10 12:00:00\nB,02,2025-01-11\n", encoding="utf-8-sig")
    uploads = tmp_path / "uploads.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["工单号", "成品编号", "上传时间"])
    ws.append(["A", "01", datetime(2025, 1, 10)])
    ws["C2"].number_format = "yyyy-mm-dd"
    wb.save(uploads)
    summary = run_receipts(receipts, uploads_path=uploads, complete_through="2025-01-11", root=tmp_path)
    assert (summary["receipts"], summary["anomalies"], summary["needs_review"]) == (2, 1, 1)
    wb = load_workbook(summary["excel"])
    header = [c.value for c in wb.active[1]]
    assert wb.active.cell(2, header.index("判定")+1).value == "待复核"
    wb.close()
    proofs = sorted((tmp_path / "evidence").rglob("receipt_*.json"))
    assert json.loads(proofs[0].read_text())["product_no"] == "01"
    assert "收货" in Path(summary["html"]).read_text()


def test_legacy_schema_and_sampled_task_cannot_prove_absence(tmp_path):
    path = tmp_path / "eip_agent.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE anomaly(id INTEGER PRIMARY KEY, trial_id INTEGER, rule_id TEXT, rule_name TEXT,
        severity TEXT, ok INTEGER NOT NULL, detail_json TEXT NOT NULL, evidence_path TEXT,
        review_status TEXT, review_note TEXT, created_at TEXT);
        INSERT INTO anomaly VALUES(1,1,'R','测试','medium',1,'{}','','已确认','旧意见','');
        """)
    db = connect(path)
    assert tuple(db.execute("SELECT ok,review_note,rule_version FROM anomaly").fetchone()) == (1,"旧意见",None)
    db.execute("INSERT INTO task(id,created_at,type,params_json,status) VALUES(1,'','collect','{}','done')")
    db.commit()
    db.close()
    receipts = tmp_path / "ledger.csv"
    receipts.write_text("workorder_no,product_no,received_at\nA,1,2025-01-01\n")
    assert run_receipts(receipts, task_id=1, root=tmp_path)["needs_review"] == 1
    with pytest.raises(ValueError, match="抽样"):
        run_receipts(receipts, task_id=1, root=tmp_path, complete_through="2025-01-02")
