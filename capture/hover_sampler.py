"""沿已配置的绘图区读取可见悬浮框并保存逐点截图。"""
from browser.pages.trial_detail import TemplateNotReady, load_templates
from ocr.fields import parse_tooltip


def _ocr(image_bytes):
    import cv2
    import numpy as np
    from ocr.engine import get_engine

    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    return get_engine()(img)


def sample_points(page, out_dir, x0, x1, y, n=60, dwell_ms=200, config=None):
    config = config if config is not None else load_templates()["trial_detail"]["curve"]
    if not config.get("tooltip") or x1 <= x0 or n < 2:
        raise TemplateNotReady("请配置有效的绘图区和悬浮框定位器")
    out_dir.mkdir(parents=True, exist_ok=True)
    tip = page.locator(config["tooltip"]).first
    points = []
    for i in range(n):
        page.mouse.move(x0 + (x1 - x0) * i / (n - 1), y)
        page.wait_for_timeout(dwell_ms)
        if not tip.is_visible():
            continue
        image = tip.screenshot(timeout=2000)
        path = out_dir / f"tip_{i:03d}.png"
        path.write_bytes(image)
        hit = parse_tooltip(tip.inner_text().splitlines())
        point = {**hit, "source": "hover_dom", "confidence": None, "evidence_path": str(path)}
        if any(hit[k] is None for k in ("ts", "kv", "pc")):
            try:
                lines = _ocr(image)
            except (ImportError, RuntimeError) as exc:
                raise TemplateNotReady(f"本地 OCR 后端不可用：{exc}") from exc
            confidence = min((s for _, s, _ in lines), default=0.0)
            decoded = parse_tooltip([line for line, _, _ in lines])
            point.update(source="hover_ocr", confidence=confidence)
            if confidence >= config.get("min_confidence", 0.90):
                for key in ("ts", "kv", "pc"):
                    if point[key] is None:
                        point[key] = decoded[key]
            else:
                point["unverified"] = decoded
        points.append(point)
    return points


def sample_curve(page, out_dir, x0, x1, y, n=60, dwell_ms=200):
    return [(p["ts"], p["kv"], p["pc"], p["confidence"], p["evidence_path"])
            for p in sample_points(page, out_dir, x0, x1, y, n, dwell_ms)
            if p["ts"] and (p["kv"] is not None or p["pc"] is not None)]
