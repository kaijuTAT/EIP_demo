"""OCR 字段解析测试：用录屏真实 OCR 噪声样本回归。

悬浮框实测格式（含各种 OCR 变体）：
    "2025-08-1919:11:16"  "KV:50.83"  "kV-43.75"  "PC3.08"  "308pC"
运行：python -m pytest tests/ -v （或 python tests/test_ocr_fields.py）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ocr.fields import (parse_kv, parse_pc, parse_step_line,  # noqa: E402
                        parse_timestamp, parse_tooltip)


def test_timestamp_variants():
    assert parse_timestamp("2025-08-1919:11:16") == "2025-08-19 19:11:16"
    assert parse_timestamp("2025.08-1919:10.51") == "2025-08-19 19:10:51"
    assert parse_timestamp("2025-08-19 19:10:51") == "2025-08-19 19:10:51"
    assert parse_timestamp("无时间戳文本") is None


def test_kv_pc_variants():
    assert parse_kv("KV:50.83") == 50.83
    assert parse_kv("kV-43.75") == 43.75
    assert parse_kv("电压 46hKV") is None
    assert parse_kv("kV ： 50.831") == 50.831
    assert parse_pc("3.08 pC") == 3.08
    assert parse_pc("308 pC") == 308  # 不猜测缺失的小数点
    assert parse_pc("PC:3.08") == 3.08
    assert parse_pc("pC3.08") == 3.08
    assert parse_kv("PC:3.08") is None  # 不串行


def test_tooltip_multiline():
    lines = ["2025-08-1919:11:16", "KV:50.83", "pC:3.08"]
    hit = parse_tooltip(lines)
    assert hit["ts"] == "2025-08-19 19:11:16"
    assert hit["kv"] == 50.83
    assert hit["pc"] == 3.08


def test_step_line():
    occ, body = parse_step_line("2025-08-1919:10:59 开始进行局放试验请升压到2倍U0-52kV")
    assert occ == "2025-08-19 19:10:59"
    assert body.startswith("开始进行局放试验")
    occ2, body2 = parse_step_line("局放背景值采集完成")
    assert occ2 is None and body2 == "局放背景值采集完成"


if __name__ == "__main__":
    test_timestamp_variants()
    test_kv_pc_variants()
    test_tooltip_multiline()
    test_step_line()
    print("全部通过")
