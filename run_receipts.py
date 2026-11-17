"""从本地收货台账出发，以工单和成品（或批次）核对上传时间。"""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles.numbers import is_datetime

from reports import write_reports
from rules.engine import VERDICTS, load_rules, timestamp
from storage.db import connect

ROOT = Path(__file__).resolve().parent
HEADERS = {"工单号": "workorder_no", "生产工单": "workorder_no", "成品编号": "product_no",
           "批次号": "batch_no", "收货时间": "received_at", "入库时间": "received_at",
           "上传时间": "uploaded_at"}


def _table(path, date_column):
    path = Path(path)
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as f:
            data = list(csv.reader(f))
    elif path.suffix.lower() == ".xlsx":
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            data = [[c.value.date() if isinstance(c.value, datetime) and is_datetime(c.number_format) == "date"
                     else c.value for c in row] for row in book.worksheets[0].iter_rows()]
        finally:
            book.close()
    else:
        raise ValueError("台账支持 UTF-8 CSV 或 XLSX")
    names = [str(n or "").strip() for n in data[0]] if data else []
    names = [HEADERS.get(n, n) for n in names]
    required = {"workorder_no", date_column}
    if not required.issubset(names) or not {"product_no", "batch_no"}.intersection(names):
        raise ValueError(f"{path.name} 须包含工单号、成品编号或批次号，以及 {date_column}")
    if any(names.count(n) > 1 for n in set(names) if n):
        raise ValueError(f"{path.name} 含重复列名")
    return [dict(zip(names, row), source_row=i) for i, row in enumerate(data[1:], 2)
            if any(v is not None and str(v).strip() for v in row)]


def _keys(row):
    order = str(row.get("workorder_no") or "").strip()
    for field in ("product_no", "batch_no"):
        value = row.get(field)
        if order and value is not None and str(value).strip():
            return [(order, field, str(value).strip())]
    return []


def _interval(value):
    if isinstance(value, datetime):
        value = value.isoformat(sep=" ")
    if isinstance(value, date):
        value = value.isoformat()
    s = str(value or "").strip()
    if len(s) == 10:
        try:
            day = date.fromisoformat(s)
            return datetime.combine(day, time.min), datetime.combine(day, time.max)
        except ValueError:
            return None
    instant = timestamp(s)
    return (instant, instant) if instant else None


def reconcile(receipts, uploads, complete_through=None):
    cutoff = _interval(complete_through)
    if complete_through and not cutoff:
        raise ValueError("完整清单截止时间须为 ISO 日期或日期时间")
    by_key = defaultdict(list)
    unkeyed = False
    for index, row in enumerate(uploads):
        keys = _keys(row)
        unkeyed |= not bool(keys)
        for key in keys:
            by_key[key].append(index)
    rows = []
    for receipt in receipts:
        keys = _keys(receipt)
        matches = [uploads[i] for i in sorted({i for k in keys for i in by_key[k]})]
        when = _interval(receipt.get("received_at"))
        detail = {"receipt_row": receipt.get("source_row"), "received_at": str(receipt.get("received_at") or ""),
                  "upload_rows": [m.get("source_row") for m in matches]}
        ok = None
        if not keys or not when:
            note = "收货记录缺少可匹配的标识或有效收货时间"
        elif not matches:
            if cutoff and not unkeyed and cutoff[1] >= when[1]:
                ok, note = False, "截至完整清单的覆盖时点仍无匹配上传记录"
            else:
                note = "本次清单未匹配，来源覆盖不足以确认漏传"
        else:
            times = {_interval(m.get("uploaded_at")) for m in matches}
            detail["uploaded_at"] = sorted({str(m.get("uploaded_at") or "") for m in matches})
            if len(times) != 1 or None in times:
                note = "匹配记录的实际上传时间缺失或不唯一"
            else:
                low, high = times.pop()
                if high <= when[0]:
                    ok, note = True, "已在收货前或收货时上传"
                elif low > when[1]:
                    ok, note = False, "实际上传晚于收货"
                else:
                    note = "同日记录的时间精度不足"
        detail["note"] = note
        rows.append({**receipt, "ok": ok, "verdict": VERDICTS[ok], "rule_id": "R-TS-01",
                     "rule_name": "数据上传时效", "detail": detail, "severity": "medium",
                     "review_status": "待复核"})
    return rows


def run_receipts(receipts_path, *, uploads_path=None, task_id=None, complete_through=None, root=None):
    root = Path(root or ROOT)
    cfg = load_rules()
    if not cfg["rules"]["R-TS-01"].get("enabled", False):
        raise ValueError("R-TS-01 已关闭")
    if bool(uploads_path) == (task_id is not None):
        raise ValueError("选择一个 EIP 上传台账或一个采集任务 ID")
    receipts = _table(receipts_path, "received_at")
    if uploads_path:
        uploads = _table(uploads_path, "uploaded_at")
        source = Path(uploads_path).name
    else:
        if complete_through:
            raise ValueError("抽样采集任务不能声明为完整上传清单")
        conn = connect(root / "eip_agent.db")
        try:
            task = conn.execute("SELECT type,status FROM task WHERE id=?", (task_id,)).fetchone()
            if not task or tuple(task) != ("collect", "done"):
                raise ValueError("请选择已完成的工单采集任务")
            uploads = [dict(r) for r in conn.execute(
                """SELECT t.id AS source_row,w.workorder_no,t.product_no,t.uploaded_at FROM trial t
                   LEFT JOIN workorder w ON w.id=t.workorder_id AND w.task_id=t.task_id WHERE t.task_id=?""",
                (task_id,))]
        finally:
            conn.close()
        source = f"采集任务 {task_id}"
    rows = reconcile(receipts, uploads, complete_through)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    evidence = root / "evidence" / f"receipts_{stamp}"
    evidence.mkdir(parents=True)
    for row in rows:
        row["rule_version"] = cfg["version"]
        proof = evidence / f"receipt_{row['source_row']}.json"
        proof.write_text(json.dumps({"receipt_file": Path(receipts_path).name, "upload_source": source,
                                    "complete_through": complete_through, **row},
                                   ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        row["evidence_items"] = [str(proof)]
    out = root / "reports" / "out" / f"收货核对_{stamp}"
    xlsx, html = write_reports(rows, out, sheet="收货核对", title="收货与试验上传核对")
    return {"receipts": len(rows), "anomalies": sum(r["ok"] is False for r in rows),
            "needs_review": sum(r["ok"] is None for r in rows),
            "excel": str(xlsx), "html": str(html)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本地收货与上传台账核对")
    parser.add_argument("--receipts", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--uploads")
    source.add_argument("--task-id", type=int)
    parser.add_argument("--complete-through", help="完整上传清单覆盖至该时点（日期表示当日结束）")
    args = parser.parse_args()
    print(json.dumps(run_receipts(args.receipts, uploads_path=args.uploads, task_id=args.task_id,
                                  complete_through=args.complete_through), ensure_ascii=False, indent=2))
