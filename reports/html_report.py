"""HTML 证据报表：每条异常一张卡片，可点开截图/录屏证据。纯本地静态文件。"""
from html import escape
from pathlib import Path

from reports.evidence import expand_evidence, relative_evidence_path

TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>EIP 质控异常报表</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;margin:24px;background:#f5f6f8}
 h1{font-size:20px;color:#1F4E79}
 .card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px 18px;margin:12px 0}
 .fail{border-left:5px solid #c0392b}.pass{border-left:5px solid #27ae60}
 .meta{color:#555;font-size:13px}.tag{display:inline-block;background:#eee;border-radius:4px;padding:1px 8px;margin-right:8px;font-size:12px}
 .sev-high{background:#c0392b;color:#fff}.sev-medium{background:#e67e22;color:#fff}
 code{background:#f0f0f0;padding:1px 4px;border-radius:3px}
 img{max-width:100%;border:1px solid #ccc;margin-top:8px}
 a{color:#1769aa;margin-right:10px}
</style></head><body>
<h1>EIP 智能质控异常报表（试点）</h1>
<p class="meta">生成时间：{generated}　共 {total} 条记录，其中异常 {fails} 条</p>
{cards}
</body></html>"""

CARD = """<div class="card {cls}">
<div><span class="tag {sevcls}">{severity}</span><b>{rule_id} {rule_name}</b>
<span class="meta">成品 {product_no} / 试验 {test_no} / {voltage_class}</span></div>
<div class="meta">判定依据：{detail}</div>
<div class="meta">证据：{evidence_links}　复核状态：{review_status}</div>
</div>"""


def write_anomaly_html(rows: list, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    fails = 0
    for row in rows:
        if row.get("ok") is False:
            fails += 1
        detail = row.get("detail", "")
        if isinstance(detail, dict):
            detail = "; ".join(
                f"{escape(str(k))}={escape(str(v))}" for k, v in detail.items()
            )
        else:
            detail = escape(str(detail))
        sev = row.get("severity", "")
        evidence = expand_evidence(row.get("evidence_items") or row.get("evidence"))
        if evidence:
            evidence_links = " ".join(
                f'<a href="{escape(relative_evidence_path(item["path"], out_path), quote=True)}" '
                f'target="_blank">{escape(str(item["label"]))}</a>'
                for item in evidence
            )
        else:
            evidence_links = "<code>暂无证据文件</code>"
        cards.append(CARD.format(
            cls="fail" if row.get("ok") is False else "pass",
            sevcls="sev-high" if sev == "high" else "sev-medium",
            severity="高" if sev == "high" else "中",
            rule_id=escape(str(row.get("rule_id", ""))),
            rule_name=escape(str(row.get("rule_name", ""))),
            product_no=escape(str(row.get("product_no", ""))),
            test_no=escape(str(row.get("test_no", ""))),
            voltage_class=escape(str(row.get("voltage_class", ""))),
            detail=detail or "—", evidence_links=evidence_links,
            review_status=escape(str(row.get("review_status", "待复核"))),
        ))
    from storage.db import now
    # CSS 花括号与 str.format 冲突，改用 replace 填充
    html = (TEMPLATE
            .replace("{generated}", now())
            .replace("{total}", str(len(rows)))
            .replace("{fails}", str(fails))
            .replace("{cards}", "\n".join(cards)))
    out_path.write_text(html, encoding="utf-8")
    return out_path
