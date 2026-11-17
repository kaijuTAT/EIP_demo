"""T1 接口适配层：把拦截到的 EIP JSON 响应按 config/api_map.yaml
映射成内部记录（工单 / 试验 / 步骤 / 曲线点）。

接口结构、字段与试验键由目标映射指定。
"""
import json
import re
from pathlib import Path

import yaml

API_MAP = Path(__file__).resolve().parents[1] / "config" / "api_map.yaml"


def load_api_map() -> dict:
    with open(API_MAP, encoding="utf-8") as f:
        return yaml.safe_load(f)


def jpath(doc, path):
    """极简 JSON 路径：'$.data.list' / '$.[0].name'。找不到返回 None。"""
    if not path or not isinstance(doc, (dict, list)):
        return None
    cur = doc
    for part in path.lstrip("$").strip(".").split("."):
        if part == "":
            continue
        if isinstance(cur, list) and part.startswith("[") and part.endswith("]"):
            idx = int(part[1:-1])
            cur = cur[idx] if 0 <= idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
        if cur is None:
            return None
    return cur


class ApiAdapter:
    """feed(url, body_bytes) → 归一化记录；按 trial_key 聚合暂存。"""

    def __init__(self, api_map: dict = None):
        self.map = api_map or load_api_map()
        self.endpoints = self.map["endpoints"]
        self.staged_workorders = []          # [dict]
        self.staged_trials = {}              # trial_key -> {"trial":…, "steps":[…], "curve":[…]}
        self.list_seen = False
        self.reported_total = None
        self.last_workorders = []
        self.manifest_position = 0
        self.unmatched = []                  # (url, bytes) 便于 D2 后补充映射

    # ── 路由 ──
    def feed(self, url: str, body: bytes, source=None):
        try:
            doc = json.loads(body)
        except Exception:
            return None
        ep, key = self._match(url)
        if not ep:
            self.unmatched.append((url, len(body)))
            return None
        parser = getattr(self, "_parse_" + ep["parser"])
        slot = parser(ep, url, doc, key)
        if source and key and isinstance(slot, dict):
            if ep["parser"] == "trial_detail":
                slot["detail_evidence"] = str(source)
            elif ep["parser"] == "trial_curve":
                slot.setdefault("curve_evidence", []).append(str(source))
                for point in slot["curve"]:
                    point.setdefault("evidence_path", str(source))
        return slot

    def _match(self, url):
        for ep in self.endpoints.values():
            if re.search(ep["url_pattern"], url):
                ex = ep.get("exclude_pattern")
                if ex and re.search(ex, url):
                    continue
                key = None
                kf = ep.get("trial_key_from_url")
                if kf:
                    m = re.search(kf, url)
                    key = m.group(1) if m else None
                return ep, key
        return None, None

    # ── 各端点解析 ──
    def _parse_workorder_list(self, ep, url, doc, _):
        items = jpath(doc, ep["items_path"])
        if not isinstance(items, list):
            return None
        self.list_seen = True
        total = jpath(doc, ep.get("total_path"))
        if str(total).isdigit():
            self.reported_total = int(total)
        out = []
        for item in items:
            rec = {k: jpath(item, p) for k, p in ep["fields"].items()}
            self.staged_workorders.append(rec)
            out.append(rec)
        self.last_workorders = out
        return out

    def _parse_trial_detail(self, ep, url, doc, key):
        if not key:
            return None
        slot = self.staged_trials.setdefault(key, {})
        trial = {k: jpath(doc, p) for k, p in ep["fields"].items()}
        slot["trial"] = trial
        sc = ep["steps"]
        steps = []
        for item in jpath(doc, sc["items_path"]) or []:
            occ, text = jpath(item, sc["occurred_at"]), jpath(item, sc["text"])
            if text:
                steps.append((occ, text))
        slot["steps"] = steps
        return slot

    def _parse_trial_curve(self, ep, url, doc, key):
        if not key:
            return None
        slot = self.staged_trials.setdefault(key, {})
        points = []
        for item in jpath(doc, ep["items_path"]) or []:
            p = {f: jpath(item, ep[f]) for f in ("ts", "kv", "pc")}
            points.append(p)
        slot.setdefault("curve", []).extend(points)
        return slot

    # ── 产出 ──
    def complete_trials(self):
        """trial_key 相同的 detail+curve 都到齐才算完整。"""
        for key, slot in self.staged_trials.items():
            if slot.get("trial") and slot.get("curve"):
                yield key, slot

    # ── 离线重放（可靠路径）──
    def replay_manifest(self, manifest_path, *, incremental=False):
        """从 ResponseSink 的 manifest.jsonl + raw/ 确定性重放解析。

        默认清空暂存并重放全部已存记录。增量模式从同一清单的上次位置继续。
        """
        manifest_path = Path(manifest_path)
        if not incremental:
            self.staged_workorders.clear()
            self.staged_trials.clear()
            self.unmatched.clear()
            self.last_workorders = []
            self.list_seen = False
            self.reported_total = None
            self.manifest_position = 0
        n = 0
        if not manifest_path.exists():
            return n
        with open(manifest_path, encoding="utf-8") as f:
            f.seek(self.manifest_position)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                raw = manifest_path.parent / rec["file"]
                if 200 <= rec.get("status", 200) < 300:
                    self.feed(rec["url"], raw.read_bytes(), source=raw)
                    n += 1
            self.manifest_position = f.tell()
        return n
