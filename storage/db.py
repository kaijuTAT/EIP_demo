"""SQLite 存储层。单机、零运维，全部数据在本机磁盘。"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "eip_agent.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,              -- probe / collect / rules_run
    params_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending/running/done/failed/paused
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS workorder (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    workorder_no TEXT, project TEXT, supplier TEXT, material TEXT,
    plan_delivery_date TEXT, progress TEXT, alarm_detail TEXT,
    source_url TEXT, captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trial (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL,
    workorder_id INTEGER,
    product_no TEXT, test_no TEXT,
    voltage_class TEXT, voltage_class_type TEXT, test_status TEXT,
    started_at TEXT, finished_at TEXT,
    detail_url TEXT, raw_json_path TEXT, captured_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trial_step (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id INTEGER NOT NULL,
    idx INTEGER NOT NULL,
    occurred_at TEXT,
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS curve_point (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kv REAL, pc REAL,
    source TEXT NOT NULL,            -- api / dom / hover_ocr / desktop_ocr
    confidence REAL,
    evidence_path TEXT
);
CREATE TABLE IF NOT EXISTS anomaly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id INTEGER NOT NULL,
    rule_id TEXT NOT NULL,
    rule_name TEXT, severity TEXT,
    ok INTEGER NOT NULL,             -- 1=通过 0=异常（本条即异常记录）
    detail_json TEXT NOT NULL,
    evidence_path TEXT,
    review_status TEXT DEFAULT '待复核',  -- 待复核/已确认/已排除/已转交
    review_note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_curve_trial ON curve_point(trial_id);
CREATE INDEX IF NOT EXISTS idx_step_trial ON trial_step(trial_id);
CREATE INDEX IF NOT EXISTS idx_anomaly_trial ON anomaly(trial_id);
"""


def connect(db_path: Path = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_task(conn, type_: str, params: dict, note: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO task(created_at, type, params_json, note) VALUES (?,?,?,?)",
        (now(), type_, json.dumps(params, ensure_ascii=False), note),
    )
    conn.commit()
    return cur.lastrowid


def insert_workorder(conn, task_id: int, rec: dict, source_url: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO workorder(task_id, workorder_no, project, supplier, material,
           plan_delivery_date, progress, alarm_detail, source_url, captured_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (task_id, rec.get("workorder_no"), rec.get("project"), rec.get("supplier"),
         rec.get("material"), rec.get("plan_delivery_date"), rec.get("progress"),
         rec.get("alarm_detail"), source_url, now()),
    )
    conn.commit()
    return cur.lastrowid


def insert_anomaly(conn, trial_id: int, result: dict, evidence_path: str = "") -> int:
    cur = conn.execute(
        """INSERT INTO anomaly(trial_id, rule_id, rule_name, severity, ok,
           detail_json, evidence_path, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (trial_id, result["rule_id"], result["rule_name"], result["severity"],
         1 if result["ok"] else 0,
         json.dumps(result["detail"], ensure_ascii=False), evidence_path, now()),
    )
    conn.commit()
    return cur.lastrowid


def insert_trial_with_steps_and_curve(
    conn, task_id: int, trial: dict, steps: list, curve: list
) -> int:
    """trial: dict 字段；steps: [(occurred_at, text)]；curve: [(ts, kv, pc, source, conf, evid)]"""
    cur = conn.execute(
        """INSERT INTO trial(task_id, product_no, test_no, voltage_class,
           voltage_class_type, test_status, captured_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            task_id, trial.get("product_no"), trial.get("test_no"),
            trial.get("voltage_class"), trial.get("voltage_class_type"),
            trial.get("test_status"), now(),
        ),
    )
    trial_id = cur.lastrowid
    for i, (occ, text) in enumerate(steps):
        conn.execute(
            "INSERT INTO trial_step(trial_id, idx, occurred_at, text) VALUES (?,?,?,?)",
            (trial_id, i, occ, text),
        )
    for ts, kv, pc, src, conf, evid in curve:
        conn.execute(
            "INSERT INTO curve_point(trial_id, ts, kv, pc, source, confidence, evidence_path)"
            " VALUES (?,?,?,?,?,?,?)",
            (trial_id, ts, kv, pc, src, conf, evid),
        )
    conn.commit()
    return trial_id
