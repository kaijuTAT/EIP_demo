"""T1 接口拦截：监听页面网络响应，把 JSON 原样落盘。

这是优先通道：若曲线/列表数据是接口返回的 JSON，这里直接拿到原始数据，
无需任何 OCR。只读响应，不修改、不重放、不重试请求。
"""
import json
import re
from pathlib import Path

from storage.db import now


class ResponseSink:
    """把命中的 JSON 响应写入 out_dir/raw/，并维护 manifest.jsonl。"""

    def __init__(self, out_dir: Path, url_patterns=None):
        self.out_dir = Path(out_dir)
        self.raw_dir = self.out_dir / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.out_dir / "manifest.jsonl"
        self.url_patterns = [re.compile(p) for p in (url_patterns or [".*"])]
        self.count = 0

    def match(self, url: str) -> bool:
        return any(p.search(url) for p in self.url_patterns)

    def save(self, url: str, status: int, body: bytes) -> dict:
        self.count += 1
        fname = f"resp_{self.count:04d}.json"
        (self.raw_dir / fname).write_bytes(body)
        # 探查曲线特征，便于 D2 快速定位数据接口
        hint = self._hint(body)
        rec = {
            "time": now(), "url": url, "status": status,
            "bytes": len(body), "file": f"raw/{fname}", "hint": hint,
        }
        with open(self.manifest_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec

    @staticmethod
    def _hint(body: bytes) -> list:
        try:
            text = body[:4000].decode("utf-8", "ignore").lower()
        except Exception:
            return []
        keys = []
        for kw in ("kv", "pc", "voltage", "discharge", "partial", "curve",
                   "time", "局放", "电压", "曲线", "workorder", "工单", "trial", "试验"):
            if kw in text:
                keys.append(kw)
        return keys


def attach(page, sink: ResponseSink):
    """挂到 Playwright page 上。返回 detach()。"""

    def on_response(resp):
        try:
            ct = resp.headers.get("content-type", "")
            if "json" not in ct.lower():
                return
            if not sink.match(resp.url):
                return
            sink.save(resp.url, resp.status, resp.body())
        except Exception:
            # 单条响应失败不影响采集
            pass

    page.on("response", on_response)

    def detach():
        page.remove_listener("response", on_response)

    return detach
