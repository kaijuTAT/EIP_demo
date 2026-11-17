"""HTML 证据报表：每条异常一张卡片，可点开截图/录屏证据。纯本地静态文件。"""
from html import escape
from pathlib import Path

from rules.engine import VERDICTS
from reports.evidence import expand_evidence, relative_evidence_path

TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>{title}</title>
<style>
 body{font-family:"Microsoft YaHei",sans-serif;margin:24px;background:#f5f6f8}
 h1{font-size:20px;color:#1F4E79}
 .card{background:#fff;border:1px solid #e0e0e0;border-radius:8px;padding:14px 18px;margin:12px 0}
 .fail{border-left:5px solid #c0392b}.unknown{border-left:5px solid #b07918}.pass{border-left:5px solid #27ae60}
 .meta{color:#555;font-size:13px}.tag{display:inline-block;background:#eee;border-radius:4px;padding:1px 8px;margin-right:8px;font-size:12px}
 .sev-high{background:#c0392b;color:#fff}.sev-medium{background:#e67e22;color:#fff}
 code{background:#f0f0f0;padding:1px 4px;border-radius:3px}
 img{max-width:100%;border:1px solid #ccc;margin-top:8px}
 a{color:#1769aa;margin-right:10px}
</style></head><body>
<h1>{title}</h1>
<p class="meta">生成时间：{generated}　共 {total} 条记录，异常线索 {fails} 条，待复核判定 {unknowns} 条</p>
{cards}
<script>
if (location.protocol === 'file:') {
  document.querySelectorAll('.review-link').forEach(a => a.href = 'http://127.0.0.1:8765' + a.getAttribute('href'));
}
</script></body></html>"""

CARD = """<div class="card {cls}">
<div><span class="tag {sevcls}">{severity}</span><b>{verdict} · {rule_id} {rule_name}</b> <span class="meta">规则 {version}</span>
<span class="meta">工单 {workorder_no} / 成品 {product_no} / 批次 {batch_no} / 试验 {test_no} / {voltage_class}</span></div>
<div class="meta">{window}</div>
<div class="meta">判定依据：{detail}</div>
<div class="meta">证据：{evidence_links}　复核状态：{review_status} {review_link}</div>
</div>"""


def write_anomaly_html(rows: list, out_path: Path, title="EIP 智能质控异常报表（试点）") -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cards = []
    fails = 0
    unknowns = 0
    for row in rows:
        if row.get("ok") is False:
            fails += 1
        elif row.get("ok") is None:
            unknowns += 1
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
        review_id = row.get("anomaly_id")
        review_link = (f'<a href="/reviews/{int(review_id)}" class="review-link">查看或记录复核意见</a>'
                       if review_id is not None else "")
        window = " 至 ".join(str(row[k]) for k in ("window_start", "window_end") if row.get(k))
        cards.append(CARD.format(
            verdict=VERDICTS[row.get("ok")], version=escape(str(row.get("rule_version") or "—")),
            workorder_no=escape(str(row.get("workorder_no") or "—")),
            batch_no=escape(str(row.get("batch_no") or "—")), window=escape(window),
            review_link=review_link,
            cls={True: "pass", False: "fail", None: "unknown"}[row.get("ok")],
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
            .replace("{title}", escape(title))
            .replace("{generated}", now())
            .replace("{total}", str(len(rows)))
            .replace("{fails}", str(fails))
            .replace("{unknowns}", str(unknowns))
            .replace("{cards}", "\n".join(cards)))
    out_path.write_text(html, encoding="utf-8")
    return out_path
