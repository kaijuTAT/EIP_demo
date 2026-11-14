"""证据文件归档与报表链接辅助函数。"""
import os
from pathlib import Path


DISPLAYABLE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".webm", ".mp4",
    ".json", ".jsonl", ".txt", ".log", ".xlsx", ".csv",
}


def collect_evidence_files(evidence_dir: Path, raw_limit: int = 5) -> list[Path]:
    """返回任务目录中适合挂到报表上的证据文件。"""
    evidence_dir = Path(evidence_dir)
    files = []
    for name in ("manifest.jsonl", "actions.log"):
        path = evidence_dir / name
        if path.is_file():
            files.append(path)
    for folder_name in ("page", "curve", "video"):
        folder = evidence_dir / folder_name
        if folder.is_dir():
            files.extend(sorted(p for p in folder.iterdir() if p.is_file()))
    raw = evidence_dir / "raw"
    if raw.is_dir():
        files.extend(sorted(p for p in raw.glob("*.json"))[:raw_limit])
    return files


def expand_evidence(raw) -> list[dict]:
    """把字符串、Path、列表和 {label,path} 统一成证据项。"""
    if raw is None or raw == "" or raw == "—":
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    items = []
    for value in values:
        if isinstance(value, dict):
            path = value.get("path") or value.get("file")
            label = value.get("label") or (Path(path).name if path else "证据")
        else:
            path = value
            label = Path(str(path)).name
        if not path:
            continue
        path = Path(path)
        if path.is_dir():
            items.extend({"label": p.name, "path": str(p)}
                         for p in collect_evidence_files(path))
        else:
            items.append({"label": str(label), "path": str(path)})
    return items


def relative_evidence_path(path: str | Path, report_path: str | Path) -> str:
    """生成相对报表文件目录的链接，兼容本地 file URL 和 Flask 静态路由。"""
    path = Path(path)
    report_path = Path(report_path)
    if not path.is_absolute():
        path = (report_path.parent / path).resolve()
    try:
        return Path(os.path.relpath(path, report_path.parent)).as_posix()
    except ValueError:
        return path.as_uri()
