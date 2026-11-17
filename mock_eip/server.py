"""Mock EIP 服务：仿真列表页/详情页 + JSON 接口，用于开发与集成测试。

启动：python -m mock_eip.server  →  http://127.0.0.1:8777
页面结构仿照 2026-11-12 录屏（绿色智造云网-智能监造-试验感知详情）。
"""
from pathlib import Path

from flask import Flask, jsonify

from mock_eip.data import TRIALS, WORKORDERS

app = Flask(__name__)

LIST_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>电工装备智慧物联平台（Mock）- 生产试验列表</title></head><body>
<h2>生产试验列表</h2>
<table border="1"><tr><th>项目</th><th>供应商</th><th>生产工单</th><th>物料</th>
<th>计划交货日期</th><th>进度</th><th>告警详情</th></tr>
{rows}</table>
<script>fetch('/api/workorders').then(r=>r.json()).then(d=>{{
  document.getElementById('cnt').textContent=d.data.list.length;
}})</script>
<p>工单数（接口加载）：<span id="cnt">…</span></p></body></html>"""

DETAIL_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>智能监造-试验感知详情（Mock）</title></head><body>
<h2>成盘局部放电耐压试验</h2>
<div id="info">加载中…</div><div id="steps"></div>
<script>
fetch('/api/trials/{key}').then(r=>r.json()).then(d=>{{
  const t=d.data;
  document.getElementById('info').innerHTML=
    '成品编号：'+t.productNo+' 试验编号：'+t.testNo+
    ' 电压等级：'+t.voltageClass+'（'+t.voltageClassType+'） 状态：'+t.testStatus;
  document.getElementById('steps').innerHTML=
    '<h3>试验步骤</h3><ol>'+t.steps.map(s=>'<li>'+s.time+' '+s.text+'</li>').join('')+'</ol>';
}});
fetch('/api/trials/{key}/curve').then(r=>r.json()).then(d=>{{
  window.__curve=d.data.points;  // 曲线数据（拦截通道的抓取目标）
}});
</script></body></html>"""


@app.route("/")
def list_page():
    rows = "".join(
        f"<tr><td>{w['projectName']}</td><td>{w['supplierName']}</td>"
        f"<td>{w['workorderNo']}</td><td>{w['materialName']}</td>"
        f"<td>{w['planDeliveryDate']}</td><td>{w['progress']}</td>"
        f"<td>{w['alarmDetail']}</td></tr>"
        for w in WORKORDERS
    )
    links = " ".join(f'<a href="/trial/{k}">{k}</a>' for k in TRIALS)
    return LIST_PAGE.format(rows=rows) + f"<p>{links}</p>"


@app.route("/trial/<key>")
def trial_page(key):
    if key not in TRIALS:
        return "not found", 404
    return DETAIL_PAGE.format(key=key)


@app.route("/api/workorders")
def api_workorders():
    return jsonify({"code": 0, "data": {"list": WORKORDERS, "total": len(WORKORDERS)}})


@app.route("/api/trials/<key>")
def api_trial(key):
    t = TRIALS.get(key)
    if not t:
        return jsonify({"code": 1, "msg": "not found"}), 404
    steps = [{"time": time, "text": text} for time, text in t["steps"]]
    return jsonify({"code": 0, "data": {
        "productNo": t["productNo"], "testNo": t["testNo"],
        "voltageClass": t["voltageClass"], "voltageClassType": t["voltageClassType"],
        "testStatus": t["testStatus"], "uploadedAt": t.get("uploadedAt"), "steps": steps,
    }})


@app.route("/api/trials/<key>/curve")
def api_trial_curve(key):
    t = TRIALS.get(key)
    if not t:
        return jsonify({"code": 1, "msg": "not found"}), 404
    pts = [{"t": ts, "kv": kv, "pc": pc} for ts, kv, pc in t["curve"]]
    return jsonify({"code": 0, "data": {"points": pts}})


def run(host="127.0.0.1", port=8777):
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run()
