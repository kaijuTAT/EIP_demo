import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from flask import Flask
from werkzeug.serving import make_server

import run_probe
from browser.collect import collect
from browser.pages.trial_detail import load_templates


@contextmanager
def local_server(app):
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_probe_dispatches_delayed_response(tmp_path, monkeypatch):
    app = Flask(__name__)

    @app.get("/")
    def page():
        return "<script>setTimeout(()=>fetch('/points'),500)</script>"

    @app.get("/points")
    def data():
        return {"points": [{"kv": 52, "pc": 3.0}]}

    monkeypatch.setattr(run_probe, "EVIDENCE_ROOT", tmp_path)
    with local_server(app) as base:
        path = run_probe.run(0.035, base, tmp_path / "profile", headless=True)
    records = [json.loads(s) for s in (path / "manifest.jsonl").read_text().splitlines()]
    assert len(records) == 1 and records[0]["url"].endswith("/points")
    assert json.loads((path / records[0]["file"]).read_text())["points"][0]["kv"] == 52
    assert list((path / "shots").glob("*.png"))


def test_dom_pagination_hover_and_persisted_review(tmp_path, monkeypatch):
    app = Flask(__name__)

    @app.get("/orders/<int:n>")
    def orders(n):
        nxt = '<a id="next" href="/orders/2">下一页</a>' if n == 1 else ''
        return f'<div class="order"><span>WO-{n}</span><a href="/detail/{n}">详情</a></div>{nxt}'

    @app.get("/detail/<int:n>")
    def trial(n):
        return f"""<p id="no">T-{n}</p><p id="prod">P-{n}</p><p id="grade">26/35</p>
        <p class="step">2025-01-01 00:00:00 试验采集开始</p>
        <div id="tip" style="position:fixed;top:150px;left:10px">等待读数</div>
        <script>document.addEventListener('mousemove',e=>{{
        const s = e.clientX < 50 ? '01' : '04';
        document.querySelector('#tip').innerText='2025-01-01 00:00:'+s+' kV:52 pC:3.08';
        }})</script>"""

    template = load_templates()
    template["workorder_list"].update(rows=".order", columns={"workorder_no": "span"}, detail_link="a",
                                     pager={"next_button": "#next", "max_pages": 3}, rate_limit_seconds=0)
    cfg = template["trial_detail"]
    cfg["fields"] = {k: {"selector": v} for k, v in
                     {"test_no": "#no", "product_no": "#prod", "voltage_class": "#grade"}.items()}
    cfg["steps"]["selector"] = ".step"
    cfg["curve"].update(tooltip="#tip", plot_area={"x0": 10, "x1": 100, "y": 90}, samples=2, dwell_ms=20)
    monkeypatch.setattr("browser.collect.ROOT", tmp_path)
    with local_server(app) as base:
        summary = collect(base, entry_url=base + "/orders/1", profile_dir=tmp_path / "profile",
                          page_wait_ms=500, capture_api=False, templates=template)
    assert summary["workorders"] == summary["visited"] == summary["trials"] == 2
    with sqlite3.connect(tmp_path / "eip_agent.db") as db:
        assert db.execute("SELECT source, COUNT(*) FROM curve_point GROUP BY source").fetchall() == [("hover_dom", 4)]
        joins = db.execute("SELECT w.workorder_no,t.test_no FROM workorder w JOIN trial t ON w.id=t.workorder_id")
        assert sorted(joins) == [("WO-1", "T-1"), ("WO-2", "T-2")]
    from app import main
    from storage.db import connect
    monkeypatch.setattr(main, "connect", lambda: connect(tmp_path / "eip_agent.db"))
    monkeypatch.setattr(main, "EVIDENCE_DIR", tmp_path / "evidence")
    client = main.app.test_client()
    row = summary["rows"][0]
    url = f"/reviews/{row['anomaly_id']}"
    assert client.get(url).status_code == 200
    assert client.post(url, data={"status": "已排除", "note": "已核对原始时段"}).status_code == 302
    assert "已核对原始时段" in client.get(url).get_data(as_text=True)
    assert Path(row["evidence"]).is_file()


def test_probe_reports_metadata_when_body_capture_is_off(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.add_url_rule("/", "page", lambda: "<script>fetch('/record?filter=example')</script>")
    app.add_url_rule("/record", "data", lambda: {"value": 3})
    monkeypatch.setattr(run_probe, "EVIDENCE_ROOT", tmp_path)
    with local_server(app) as base:
        out = run_probe.run(0.015, base, tmp_path / "profile", headless=True, url_patterns=[])
    assert not list((out / "raw").glob("*.json"))
    assert json.loads((out / "response_types.json").read_text()) == [
        {"path": "/record", "http_status": 200, "count": 1}]


def test_hover_ocr_keeps_low_confidence_for_review(tmp_path, monkeypatch):
    from browser.ctx import BrowserSession
    from capture.hover_sampler import sample_points
    score = 0.83
    lines = ["2025-08-19 19:11:16", "kV:50.83", "pC:3.08"]
    monkeypatch.setattr("capture.hover_sampler._ocr", lambda img: [(s, score, None) for s in lines])
    with BrowserSession(headless=True, profile_dir=tmp_path / "profile") as browser:
        browser.page.set_content('<div id="tip" style="width:150px;height:50px">曲线</div>')
        config = {"tooltip": "#tip"}
        def read():
            return sample_points(browser.page, tmp_path / "tips", 10, 30, 20, 2, 10, config)[0]
        low = read()
        assert low["pc"] is None and low["unverified"]["pc"] == 3.08
        assert Path(low["evidence_path"]).is_file()
        config["min_confidence"] = 0.80
        accepted = read()
        assert (accepted["ts"], accepted["kv"], accepted["pc"]) == (lines[0], 50.83, 3.08)
        assert accepted["source"] == "hover_ocr"


def test_missing_pagination_does_not_report_complete(tmp_path, monkeypatch):
    import pytest
    from browser.pages.trial_detail import TemplateNotReady
    app = Flask(__name__)
    app.add_url_rule("/", "page", lambda: "<script>fetch('/api/workorders')</script>")
    app.add_url_rule("/api/workorders", "orders", lambda: {
        "data": {"list": [{"workorderNo": "A", "detailUrl": "/a"}], "total": 5}})
    monkeypatch.setattr("browser.collect.ROOT", tmp_path)
    with local_server(app) as base, pytest.raises(TemplateNotReady, match="列表报告 5"):
        collect(base, profile_dir=tmp_path / "profile")
    with sqlite3.connect(tmp_path / "eip_agent.db") as db:
        assert db.execute("SELECT status FROM task").fetchone() == ("paused",)
        assert db.execute("SELECT COUNT(*) FROM trial").fetchone() == (0,)
