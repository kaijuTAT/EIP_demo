"""同一任务的两个报表均写完后再发布。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from reports.excel_report import write_anomaly_excel
from reports.html_report import write_anomaly_html


def write_reports(rows, base, *, sheet="异常清单", title="EIP 智能质控异常报表（试点）"):
    base = Path(base)
    base.parent.mkdir(parents=True, exist_ok=True)
    xlsx, html = base.with_suffix(".xlsx"), base.with_suffix(".html")
    # 暂存目录和 out 同层，写入的相对证据链接在发布后继续有效。
    with TemporaryDirectory(dir=base.parent.parent) as td:
        staged = Path(td)
        write_anomaly_excel(rows, staged / xlsx.name, sheet_name=sheet)
        write_anomaly_html(rows, staged / html.name, title=title)
        (staged / xlsx.name).replace(xlsx)
        try:
            (staged / html.name).replace(html)
        except OSError:
            xlsx.unlink(missing_ok=True)
            raise
    return xlsx, html
