"""最后兜底：pyautogui 真鼠标 + mss 全屏截图采样。

仅当页面不认 Playwright 合成鼠标事件（悬浮框不弹）时启用。
警告：真鼠标会抢用户光标，运行期间人不得操作该电脑；建议在配置的专用机上跑。
"""
import time
from pathlib import Path

import cv2
import numpy as np


def desktop_sample_curve(out_dir, x0: int, x1: int, y: int, n: int = 60,
                         dwell_ms: int = 250, region=None) -> list:
    """真鼠标沿 (x0,y)-(x1,y) 滑动，截取 region=(left,top,w,h) 内悬浮框并 OCR。

    坐标为屏幕绝对像素。返回与 hover_sampler 相同结构的点序列。
    """
    import pyautogui  # 延迟导入：不用兜底就不依赖
    from mss import mss

    from ocr.engine import get_engine
    from ocr.fields import parse_tooltip

    pyautogui.FAILSAFE = True  # 鼠标甩到屏幕左上角可紧急中止
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    eng = get_engine()
    points = []
    with mss() as sct:
        for i, x in enumerate(np.linspace(x0, x1, n)):
            pyautogui.moveTo(int(x), int(y), duration=0.05)
            time.sleep(dwell_ms / 1000)
            shot = sct.grab({"left": region[0], "top": region[1],
                             "width": region[2], "height": region[3]} if region
                            else {"left": 0, "top": 0, "width": sct.width, "height": sct.height})
            img = np.asarray(shot)[:, :, :3]  # mss 是 BGRA
            ev = out_dir / f"ds_{i:03d}.png"
            ok, buf = cv2.imencode(".png", img)
            if ok:
                ev.write_bytes(buf.tobytes())
            lines = eng(img)
            hit = parse_tooltip([t for t, s, b in lines])
            conf = float(np.mean([s for t, s, b in lines])) if lines else 0.0
            if hit["kv"] is not None or hit["pc"] is not None:
                points.append((hit["ts"], hit["kv"], hit["pc"], round(conf, 3), str(ev)))
    return points
