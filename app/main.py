"""本地管理界面和单机任务队列。

启动：python -m app.main
默认监听 127.0.0.1:8765，不把任务、页面内容或证据发送到外部服务。
"""
import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from flask import abort, url_for
from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory

from browser.collect import collect
from browser.pages.trial_detail import TemplateNotReady
from run_demo import run_demo
from run_probe import run as run_probe
from run_receipts import run_receipts
from storage.db import connect, create_task, update_task

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports" / "out"
EVIDENCE_DIR = ROOT / "evidence"
PROFILE_DIR = EVIDENCE_DIR / "browser_profile"
TARGETS_FILE = ROOT / "config" / "targets.yaml"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="eip-agent")
_MOCK_SERVER = None
_MOCK_SERVER_LOCK = threading.Lock()

TYPE_NAMES = {"probe": "流量验证", "collect": "工单采集", "rules_run": "样例规则演示", "receipts": "收货台账核对"}

PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>EIP 质控 Agent</title>
<style>
body{font-family:"Microsoft YaHei";margin:24px;background:#f7f8fa;color:#222}
h2{color:#1F4E79} .panel{background:#fff;border:1px solid #ddd;border-radius:8px;padding:16px;margin-bottom:18px}
table{border-collapse:collapse;width:100%;background:#fff}td,th{border:1px solid #ddd;padding:7px 9px;font-size:13px;vertical-align:top}
th{background:#1F4E79;color:#fff}.tag{padding:2px 8px;border-radius:4px;background:#eee;white-space:nowrap}
.running{background:#e67e22;color:#fff}.done{background:#27ae60;color:#fff}.failed{background:#c0392b;color:#fff}.paused{background:#b07918;color:#fff}.pending{background:#8e9aa6;color:#fff}
label{display:inline-block;margin:4px 12px 4px 0;font-size:13px}input,select,button{padding:5px 7px;margin-left:4px}
button{background:#1F4E79;color:#fff;border:0;border-radius:4px;cursor:pointer}.muted{color:#666;font-size:12px}
a{color:#1769aa}.result{max-width:420px;word-break:break-all}
</style></head><body>
<h2>EIP 本地智能质控 Agent</h2>
<div class="panel">
<form method="post" action="/tasks">
  <label>任务<select name="type"><option value="collect">工单采集</option><option value="probe">流量验证</option><option value="rules_run">样例规则演示</option><option value="receipts">收货台账核对</option></select></label>
  <label>目标<select name="target">{% for key, target in targets.items() %}<option value="{{key}}">{{target.name}}</option>{% endfor %}</select></label>
  <label>入口 URL<input name="entry_url" size="42" placeholder="留空使用目标配置"></label>
  <label>基础 URL<input name="base_url" size="32" placeholder="真实 EIP 地址"></label>
  <label>详情数<input name="limit" type="number" min="1" max="200" value="20" size="5"></label>
  <label>探查分钟<input name="minutes" type="number" min="1" max="120" value="15" size="5"></label>
  <label>浏览器<select name="headless"><option value="0">显示窗口，适合真实 EIP</option><option value="1">后台运行，适合 Mock</option></select></label>
  <label>备注<input name="note" size="28" placeholder="品类/日期范围"></label>
  <fieldset id="receipt-fields" hidden><legend>本地收货台账</legend>
    <label>收货 CSV/XLSX 路径<input name="receipts_path" size="35"></label>
    <label>上传 CSV/XLSX 路径<input name="uploads_path" size="35"></label>
    <label>或已完成的采集任务 ID<input type="number" min="1" name="collect_task"></label>
    <label>完整上传清单截止时间<input name="complete_through" placeholder="可选，YYYY-MM-DD HH:MM:SS"></label>
  </fieldset>
  <script>const taskType=document.querySelector('[name=type]');
  taskType.onchange=()=>document.querySelector('#receipt-fields').hidden=taskType.value!=='receipts';</script>
  <button type="submit">启动任务</button>
</form>
<div class="muted">真实 EIP 首次运行先人工登录。采集只读页面，不点击提交、采集和修改类按钮。</div>
</div>
<table><tr><th>ID</th><th>类型</th><th>状态</th><th>备注</th><th>创建时间</th><th>结果</th></tr>
{% for t in tasks %}<tr><td>{{t.id}}</td><td>{{t.type_name}}</td>
<td><span class="tag {{t.status}}">{{t.status}}</span></td><td>{{t.note}}</td><td>{{t.created_at}}</td>
<td class="result">{{t.result_text}}</td></tr>{% endfor %}</table>
<p><a href="/reports">查看本地报表</a>　<span class="muted">页面每 8 秒刷新一次</span></p>
<script>let editing=false;document.querySelector('form').addEventListener('input',()=>editing=true);
setTimeout(()=>{if(!editing) location.reload()},8000);</script></body></html>"""


def _load_targets() -> dict:
    if not TARGETS_FILE.exists():
        return {"mock": {"name": "本地 Mock", "base_url": "http://127.0.0.1:8777", "entry_url": ""}}
    with TARGETS_FILE.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("targets", {})


def _int_value(value, default):
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _ensure_mock_server(base_url: str) -> None:
    """管理页选择 Mock 目标时自动启动本地仿真服务。"""
    global _MOCK_SERVER
    if base_url.rstrip("/") != "http://127.0.0.1:8777":
        return
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=0.3):
            return
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    with _MOCK_SERVER_LOCK:
        if _MOCK_SERVER is not None:
            return
        from werkzeug.serving import make_server
        from mock_eip.server import app as mock_app

        _MOCK_SERVER = make_server("127.0.0.1", 8777, mock_app)
        threading.Thread(target=_MOCK_SERVER.serve_forever,
                         name="mock-eip", daemon=True).start()


def _task_rows():
    conn = connect()
    rows = []
    for row in conn.execute("SELECT * FROM task ORDER BY id DESC LIMIT 100"):
        item = dict(row)
        try:
            params = json.loads(item.get("params_json") or "{}")
        except json.JSONDecodeError:
            params = {}
        result = params.get("result")
        if result:
            item["result_text"] = json.dumps(result, ensure_ascii=False)
        elif params.get("error"):
            item["result_text"] = str(params["error"])
        else:
            item["result_text"] = "等待执行" if item["status"] == "pending" else ""
        item["type_name"] = TYPE_NAMES.get(item["type"], item["type"])
        rows.append(item)
    conn.close()
    return rows


def _run_task(task_id: int, task_type: str, params: dict, note: str):
    if task_type != "collect":
        conn = connect()
        update_task(conn, task_id, "running")
        conn.close()
    try:
        target = _load_targets().get(params.get("target"), {})
        if task_type == "collect":
            if params.get("target") == "mock":
                _ensure_mock_server(params["base_url"])
            collect(
                params["base_url"],
                limit=params.get("limit"),
                headless=params.get("headless", False),
                task_note=note,
                task_id=task_id,
                entry_url=params.get("entry_url") or None,
                profile_dir=PROFILE_DIR, manual_login=target.get("manual_login", False),
                capture_api=target.get("capture_api", True),
            )
            return  # 采集器将报表、业务记录和任务状态一并提交。
        elif task_type == "probe":
            if params.get("target") == "mock":
                _ensure_mock_server(params["base_url"])
            out_dir = run_probe(
                minutes=params.get("minutes", 15),
                start_url=params.get("entry_url") or params.get("base_url") or None,
                profile_dir=PROFILE_DIR, base_url=params.get("base_url"),
                url_patterns=target.get("probe_url_patterns"), headless=params.get("headless", False),
            )
            result = {"evidence": str(out_dir)}
        elif task_type == "receipts":
            result = run_receipts(params["receipts_path"], uploads_path=params.get("uploads_path"),
                                  task_id=params.get("collect_task"), complete_through=params.get("complete_through"))
        elif task_type == "rules_run":
            result = run_demo(task_id=task_id)
        else:
            raise ValueError(f"不支持的任务类型：{task_type}")
        conn = connect()
        update_task(conn, task_id, "done", result=result)
        conn.close()
    except Exception as exc:
        conn = connect()
        update_task(conn, task_id, "paused" if isinstance(exc, TemplateNotReady) else "failed",
                    error=f"{type(exc).__name__}: {exc}")
        conn.close()


@app.route("/")
def index():
    return render_template_string(PAGE, tasks=_task_rows(), targets=_load_targets())


@app.route("/tasks", methods=["POST"])
def new_task():
    targets = _load_targets()
    task_type = request.form.get("type", "collect")
    target_key = request.form.get("target", "mock")
    target = targets.get(target_key, {})
    base_url = (request.form.get("base_url") or target.get("base_url") or "").strip()
    entry_url = (request.form.get("entry_url") or target.get("entry_url") or "").strip()
    if not base_url and task_type == "collect":
        return "请填写基础 URL 或在 config/targets.yaml 配置目标", 400
    params = {
        "target": target_key,
        "base_url": base_url,
        "entry_url": entry_url,
        "limit": _int_value(request.form.get("limit"), 20),
        "minutes": _int_value(request.form.get("minutes"), 15),
        "headless": request.form.get("headless", "0") == "1",
    }
    if task_type == "receipts":
        params.update(receipts_path=request.form.get("receipts_path", "").strip(),
                      uploads_path=request.form.get("uploads_path", "").strip(),
                      collect_task=request.form.get("collect_task", type=int),
                      complete_through=request.form.get("complete_through", "").strip())
        if not params["receipts_path"] or bool(params["uploads_path"]) == bool(params["collect_task"]):
            return "请填写收货台账，并选择上传台账或采集任务 ID 之一", 400
    note = request.form.get("note", "").strip()
    conn = connect()
    task_id = create_task(conn, task_type, params, note=note)
    conn.close()
    EXECUTOR.submit(_run_task, task_id, task_type, params, note)
    return redirect("/")


@app.route("/reports")
def reports():
    files = []
    if REPORTS_DIR.exists():
        files = sorted(REPORTS_DIR.glob("*.xlsx")) + sorted(REPORTS_DIR.glob("*.html"))
    links = "<br>".join(
        f'<a target="_blank" href="/reports/{f.name}">{f.name}</a>' for f in files
    ) or "暂无报表"
    return f"<h3>本地报表</h3><p>{links}</p><p><a href='/'>返回任务页</a></p>"


@app.route("/reports/<path:name>")
def report_file(name):
    suffix = Path(name).suffix.lower()
    return send_from_directory(REPORTS_DIR, name, as_attachment=suffix not in {".html", ".htm"})


@app.route("/evidence/<path:name>")
def evidence_file(name):
    if Path(name).parts and Path(name).parts[0] == "browser_profile":
        abort(404)
    return send_from_directory(EVIDENCE_DIR, name, as_attachment=False)


REVIEW_STATUSES = ("待复核", "已确认", "已排除", "已转交")


@app.route("/reviews/<int:anomaly_id>", methods=["GET", "POST"])
def review(anomaly_id):
    conn = connect()
    try:
        record = conn.execute(
            """SELECT a.*,t.product_no,t.test_no,w.workorder_no FROM anomaly a
               JOIN trial t ON t.id=a.trial_id LEFT JOIN workorder w ON w.id=t.workorder_id
               WHERE a.id=?""", (anomaly_id,)).fetchone()
        if not record:
            abort(404)
        if request.method == "POST":
            status = request.form.get("status")
            if status not in REVIEW_STATUSES:
                return "无效的复核状态", 400
            with conn:
                conn.execute("UPDATE anomaly SET review_status=?,review_note=? WHERE id=?",
                             (status, request.form.get("note", "").strip(), anomaly_id))
            return redirect(url_for("review", anomaly_id=anomaly_id))
        proof = Path(record["evidence_path"] or "").resolve()
        proof_name = proof.relative_to(EVIDENCE_DIR.resolve()).as_posix() if (
            proof.is_file() and proof.is_relative_to(EVIDENCE_DIR.resolve())) else None
        return render_template_string("""<!doctype html><html lang="zh"><meta charset="utf-8">
        <title>试验复核</title><h2>{{r.rule_id}} {{r.rule_name}}</h2>
        <p>工单 {{r.workorder_no or '—'}}　成品 {{r.product_no or '—'}}　试验 {{r.test_no or '—'}}</p>
        <p>{{r.window_start or ''}} 至 {{r.window_end or ''}}　规则 {{r.rule_version or '—'}}</p>
        <pre>{{r.detail_json}}</pre>{% if proof %}<p><a href="{{url_for('evidence_file',name=proof)}}">本条判定的证据</a></p>{% endif %}
        <form method="post"><label>复核状态 <select name="status">{% for s in statuses %}
        <option {% if s==r.review_status %}selected{% endif %}>{{s}}</option>{% endfor %}</select></label>
        <p><label>复核意见<br><textarea name="note" rows="5" cols="65">{{r.review_note}}</textarea></label></p>
        <button>保存复核意见</button></form></html>""", r=record, proof=proof_name, statuses=REVIEW_STATUSES)
    finally:
        conn.close()


@app.route("/api/tasks")
def api_tasks():
    return jsonify(_task_rows())


def main(host="127.0.0.1", port=8765, open_browser=True):
    if open_browser:
        import webbrowser
        webbrowser.open(f"http://{host}:{port}")
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
