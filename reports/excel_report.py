"""Excel 异常报表：复核清单 + 双周台账样式。"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from reports.evidence import expand_evidence, relative_evidence_path

HEAD_FILL = PatternFill("solid", fgColor="1F4E79")
HEAD_FONT = Font(color="FFFFFF", bold=True)
FAIL_FILL = PatternFill("solid", fgColor="FFC7CE")

COLUMNS = [
    ("trial_id", "试验ID", 8), ("product_no", "成品编号", 18), ("test_no", "试验编号", 20),
    ("voltage_class", "电压等级", 10), ("rule_id", "规则编号", 10), ("rule_name", "异常类型", 18),
    ("severity", "严重度", 8), ("detail", "判定依据", 46), ("review_status", "复核状态", 10),
    ("review_note", "复核意见", 16), ("evidence", "证据链接", 30),
]


def write_anomaly_excel(rows: list, out_path: Path, sheet_name: str = "异常清单") -> Path:
    """rows: dict，键与 COLUMNS 第一列一致。ok=False 的行红底。"""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    for i, (_, name, width) in enumerate(COLUMNS, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.fill, c.font = HEAD_FILL, HEAD_FONT
        c.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"

    for r, row in enumerate(rows, 2):
        for i, (key, _, _) in enumerate(COLUMNS, 1):
            val = row.get(key, "")
            if key == "detail" and isinstance(val, dict):
                val = "; ".join(f"{k}={v}" for k, v in val.items())
            c = ws.cell(row=r, column=i, value=val)
            c.alignment = Alignment(vertical="top", wrap_text=(key == "detail"))
            if key == "evidence":
                items = expand_evidence(row.get("evidence_items") or val)
                if items:
                    c.value = items[0]["label"]
                    c.hyperlink = relative_evidence_path(items[0]["path"], out_path)
                    c.style = "Hyperlink"
                    if len(items) > 1:
                        extra = "\n".join(
                            f"{item['label']}: {item['path']}" for item in items[1:]
                        )
                        c.comment = Comment(
                            "其他证据文件\n" + extra, "EIP Agent"
                        )
            if row.get("ok") is False:
                c.fill = FAIL_FILL
    wb.save(out_path)
    return out_path
