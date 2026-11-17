"""OCR 文本 → 结构化字段：时间戳、kV、pC、试验步骤行。

实测悬浮框格式（2026-11-12 录屏 OCR）：
    "2025-08-1919:11:16" / "KV:50.83" / "pC:3.08"
OCR 常见噪声：冒号丢失（191051）、KV-/PC- 变体、全角字符。
"""
import re
import unicodedata
from datetime import datetime

# "2025-08-1919:10:51" / "2025.08-1919:10.51" / "2025-08-19 19:10:51"
TS_RE = re.compile(
    r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?\s*(\d{1,2})[:.时](\d{2})[:.分\-]?(\d{2})?"
)
VALUE = r"(\d+(?:[.,]\d+)?)"
KV_RE = re.compile(r"kv\s*[:\-—]?\s*" + VALUE, re.I)
PC_RE = re.compile(r"pc\s*[:\-—]?\s*" + VALUE, re.I)
KV_SUFFIX = re.compile(r"(?<![\d:.,])" + VALUE + r"\s*kv\b", re.I)
PC_SUFFIX = re.compile(r"(?<![\d:.,])" + VALUE + r"\s*pc\b", re.I)


def _num(s: str) -> float:
    return float(s.replace(",", ".").replace("。", "."))


def parse_timestamp(text: str):
    """从一段 OCR 文本中提取第一个时间戳，返回 'YYYY-MM-DD HH:MM:SS' 或 None。"""
    m = TS_RE.search(text)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    try:
        return datetime(
            int(y), int(mo), int(d), int(h), int(mi), int(s or 0)
        ).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_kv(text: str):
    text = unicodedata.normalize("NFKC", text)
    m = KV_RE.search(text) or KV_SUFFIX.search(text)
    return _num(m.group(1)) if m else None


def parse_pc(text: str):
    text = unicodedata.normalize("NFKC", text)
    m = PC_RE.search(text) or PC_SUFFIX.search(text)
    return _num(m.group(1)) if m else None


def parse_tooltip(lines: list):
    """悬浮框多行 OCR → {'ts','kv','pc'}。lines: [(text, score)] 或 [str]。"""
    texts = [x[0] if isinstance(x, (tuple, list)) else x for x in lines]
    joined = " ".join(texts)

    def field(parser):
        for line in texts:
            value = parser(line)
            if value is not None:
                return value
        return parser(joined)

    return {"ts": field(parse_timestamp), "kv": field(parse_kv), "pc": field(parse_pc),
            "raw": joined.strip()}


def parse_step_line(text: str):
    """步骤行 'YYYY-MM-DDHH:MM:SS 步骤文案' → (occurred_at, 文案)；无时间戳返回 (None, 原文)。"""
    m = TS_RE.search(text)
    if not m:
        return None, text.strip()
    ts = parse_timestamp(text)
    body = TS_RE.sub("", text, count=1).strip(" ：:,-—.")
    return ts, body


def parse_step_lines(lines: list):
    """整页步骤时间线。返回 [(occurred_at, text)]，保持原顺序。"""
    out = []
    for x in lines:
        text = x[0] if isinstance(x, (tuple, list)) else x
        occ, body = parse_step_line(text)
        if body:
            out.append((occ, body))
    return out


def normalize_for_chain(text) -> str:
    """步骤链匹配前的归一化：去空白/全角冒号，统一数字间隔。容忍 YAML 把纯数字关键词解析成 int。"""
    return (
        str(text).replace(" ", "")
        .replace("\u3000", "")
        .replace("：", ":")
        .replace("—", "-")
        .replace("，", ",")
    )
