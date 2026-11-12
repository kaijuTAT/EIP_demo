"""T3 曲线悬浮框采样（Playwright 虚拟鼠标）。

实测悬浮框内容：时间 + KV:xx.xx + pC:xx.xx（见 2026-11-12 录屏）。
按 X 轴等分移动虚拟鼠标，截悬浮框小图 → 本地 OCR → (ts, kv, pc) 点序列。
"""
import time

import cv2
import numpy as np

from ocr.engine import get_engine
from ocr.fields import parse_tooltip


def _img_from_bytes(data: bytes):
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def sample_curve(page, out_dir, x0: float, x1: float, y: float,
                 n: int = 60, dwell_ms: int = 200) -> list:
    """返回 [(ts, kv, pc, confidence, evidence_path)]。tooltip 截图存 out_dir。

    x0/x1/y：曲线绘图区像素坐标（相对页面，D2 实测后回填到 page_templates.yaml）。
    """
    from browser.pages.trial_detail import load_templates, TemplateNotReady

    tpl = load_templates()
    curve = tpl["trial_detail"]["curve"]
    tip_sel = curve.get("tooltip", "")
    if not tip_sel:
        raise TemplateNotReady("trial_detail.curve.tooltip 未回填，请先完成 D2 验证")

    out_dir.mkdir(parents=True, exist_ok=True)
    eng = get_engine()
    tip = page.locator(tip_sel).first
    points = []
    xs = np.linspace(x0, x1, n)
    for i, x in enumerate(xs):
        page.mouse.move(float(x), float(y))
        time.sleep(dwell_ms / 1000)
        try:
            img_bytes = tip.screenshot()
        except Exception:
            continue
        ev = out_dir / f"tip_{i:03d}.png"
        # 中文路径下 cv2.imwrite 会静默失败，统一 imencode + 原生写盘
        ok, buf = cv2.imencode(".png", _img_from_bytes(img_bytes))
        if ok:
            ev.write_bytes(buf.tobytes())
        lines = eng(_img_from_bytes(img_bytes))
        hit = parse_tooltip([t for t, s, b in lines])
        conf = float(np.mean([s for t, s, b in lines])) if lines else 0.0
        if hit["kv"] is not None or hit["pc"] is not None:
            points.append((hit["ts"], hit["kv"], hit["pc"], round(conf, 3), str(ev)))
    return points
