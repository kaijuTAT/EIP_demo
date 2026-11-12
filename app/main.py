"""本地管理界面。只监听 127.0.0.1（数据不出网红线）。

启动：python -m app.main    →  http://127.0.0.1:8765
功能：任务列表/新建、进度查看、报表下载（试点版最小界面）。
"""
import sqlite3
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory

from storage.db import connect, create_task, now

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports" / "out"
EVIDENCE_DIR = ROOT / "evidence"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>EIP 质控 Agent</title>
<style>body{font-family:"Microsoft YaHei";margin:24px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ddd;padding:6px 10px;font-size:13px}th{background:#1F4E79;color:#fff}
.tag{padding:1px 8px;border-radius:4px;background:#eee}.running{background:#e67e22;color:#fff}
.done{background:#27ae60;color:#fff}.failed{background:#c0392b;color:#fff}
form{margin:16px 0}select,input,button{margin-right:8px}</style></head><body>
<h2>EIP 本地智能质控 Agent（127.0.0.1 专用）</h2>
<form method="post" action="/tasks">
  <select name="type"><option value="probe">流量验证</option>
  <option value="collect">工单采集</option><option value="rules_run">规则跑批</option></select>
  <input name="note" placeholder="备注（品类/日期范围）" size="30">
  <button>新建任务</button></form>
<table><tr><th>ID</th><th>类型</th><th>状态</th><th>备注</th><th>创建时间</th></tr>
{% for t in tasks %}<tr><td>{{t.id}}</td><td>{{t.type}}</td>
<td><span class="tag {{t.status}}">{{t.status}}</span></td>
<td>{{t.note}}</td><td>{{t.created_at}}</td></tr>{% endfor %}</table>
<p><a href="/reports">报表下载</a></p></body></html>"""


@app.route("/")
def index():
    conn = connect()
    rows = conn.execute("SELECT * FROM task ORDER BY id DESC LIMIT 100").fetchall()
    conn.close()
    return render_template_string(PAGE, tasks=rows)


@app.route("/tasks", methods=["POST"])
def new_task():
    conn = connect()
    create_task(conn, request.form.get("type", "probe"), {},
                note=request.form.get("note", ""))
    conn.close()
    return redirect("/")


@app.route("/reports")
def reports():
    files = sorted(REPORTS_DIR.glob("*.xlsx")) + sorted(REPORTS_DIR.glob("*.html")) \
        if REPORTS_DIR.exists() else []
    links = "<br>".join(f'<a href="/reports/{f.name}">{f.name}</a>' for f in files) or "暂无报表"
    return f"<h3>报表</h3><p>{links}</p><p><a href='/'>返回</a></p>"


@app.route("/reports/<path:name>")
def report_file(name):
    return send_from_directory(REPORTS_DIR, name, as_attachment=True)


@app.route("/api/tasks")
def api_tasks():
    conn = connect()
    rows = [dict(r) for r in conn.execute("SELECT * FROM task ORDER BY id DESC LIMIT 100")]
    conn.close()
    return jsonify(rows)


def main(host="127.0.0.1", port=8765):
    webbrowser.open(f"http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
