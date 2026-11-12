"""OCR 引擎封装：PaddleOCR 官方优先，装不上自动退到 RapidOCR（同一套 Paddle 模型的
onnx 离线版）。全部本地推理，零外发。

用法：
    eng = get_engine()
    result = eng(image)   # image: ndarray / 图片路径 / bytes
    # result: [ (text, score, box) ]
"""
import threading

_lock = threading.Lock()
_engine = None
_backend = None


def _try_paddleocr():
    try:
        from paddleocr import PaddleOCR  # noqa
    except Exception:
        return None
    # Python 3.13 或缺 paddlepaddle 时这里会抛错
    try:
        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
    except Exception:
        return None

    class _Paddle:
        backend = "paddleocr"

        def __call__(self, img):
            res = ocr.ocr(img, cls=True)
            out = []
            for page in res or []:
                for line in page or []:
                    box, (text, score) = line[0], line[1]
                    out.append((text, float(score), box))
            return out

    return _Paddle()


def _try_rapidocr():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return None
    ocr = RapidOCR()

    class _Rapid:
        backend = "rapidocr"

        def __call__(self, img):
            # rapidocr 接受 ndarray/路径/bytes；结果行: [box, text, score]
            result, _ = ocr(img)
            if not result:
                return []
            return [(r[1], float(r[2]), r[0]) for r in result]

    return _Rapid()


def get_engine():
    """单例。优先 PaddleOCR，失败退 RapidOCR。"""
    global _engine, _backend
    with _lock:
        if _engine is not None:
            return _engine
        for factory in (_try_paddleocr, _try_rapidocr):
            eng = factory()
            if eng is not None:
                _engine, _backend = eng, eng.backend
                return _engine
        raise RuntimeError("无可用 OCR 引擎：paddleocr 与 rapidocr 均不可用，请先安装其一")


def backend_name() -> str:
    get_engine()
    return _backend
