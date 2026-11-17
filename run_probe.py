"""D2 人工阅页诊断。浏览器操作及响应派发始终留在同一线程。"""
import argparse
from collections import Counter
import json
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from browser.ctx import BrowserSession
from browser.intercept import ResponseSink, attach

EVIDENCE_ROOT = Path(__file__).resolve().parent / "evidence"
CURVE_HINTS = {"kv", "pc", "voltage", "discharge", "partial", "curve", "局放", "电压", "曲线"}


def _wait_for_enter(stop):
    try:
        input("按回车结束采集 > ")
    except EOFError:
        pass
    finally:
        stop.set()


def run(minutes=None, start_url=None, profile_dir=None, *, headless=False,
        url_patterns=None, base_url=None):
    origin = base_url or start_url
    host = urlsplit(origin or "").hostname
    if url_patterns is None and host not in ("127.0.0.1", "localhost", "::1"):
        url_patterns = []
    out_dir = EVIDENCE_ROOT / f"probe_{datetime.now():%Y%m%d_%H%M%S_%f}"
    sink = ResponseSink(out_dir, url_patterns=url_patterns, origin=origin)
    shots = out_dir / "shots"
    shots.mkdir()
    observed = Counter()
    site = urlsplit(origin or "")

    def record_type(response):
        # 未配置正文留存的响应只计路径、状态和次数，查询参数不进诊断清单。
        url = urlsplit(response.url)
        if origin and (url.scheme, url.netloc) == (site.scheme, site.netloc):
            if "json" in response.headers.get("content-type", "").lower():
                observed[url.path, response.status] += 1

    stop = threading.Event()
    with BrowserSession(headless=headless, profile_dir=profile_dir) as session:
        detach = attach(session.context, sink)
        session.context.on("response", record_type)
        try:
            if start_url:
                session.page.goto(start_url, wait_until="domcontentloaded")
            if minutes is None:
                threading.Thread(target=_wait_for_enter, args=(stop,), daemon=True).start()
            elif minutes <= 0:
                raise ValueError("探查时长须大于零")
            deadline = time.monotonic() + minutes * 60 if minutes is not None else float("inf")
            next_shot = 0
            index = 0
            while not stop.is_set() and time.monotonic() < deadline and session.context.pages:
                page = session.context.pages[-1]
                if time.monotonic() >= next_shot:
                    # 登录页和外部跳转不作截图。仅留存配置范围内的业务阅页证据。
                    on_target = origin and urlsplit(page.url).netloc == urlsplit(origin).netloc
                    if on_target and (host in ("127.0.0.1", "localhost", "::1") or sink.count):
                        page.screenshot(path=str(shots / f"page_{index:03d}.png"), timeout=5000)
                        index += 1
                        next_shot = time.monotonic() + 30
                page.wait_for_timeout(100)
        except KeyboardInterrupt:
            pass
        finally:
            detach()
            session.context.remove_listener("response", record_type)
            (out_dir / "response_types.json").write_text(json.dumps(
                [{"path": path, "http_status": status, "count": count}
                 for (path, status), count in observed.items()], ensure_ascii=False, indent=2), encoding="utf-8")
    summarize(out_dir, sink)
    return out_dir


def summarize(out_dir, sink):
    candidates = []
    for line in sink.manifest_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if CURVE_HINTS.intersection(rec.get("hint", [])):
            candidates.append(rec)
    print(f"保存 {sink.count} 份业务响应，曲线候选 {len(candidates)} 份。证据目录 {out_dir}")
    for rec in candidates[:15]:
        print(f"  {rec['url']}  {rec['file']}")
    if sink.errors:
        (out_dir / "errors.json").write_text(json.dumps(sink.errors, ensure_ascii=False), encoding="utf-8")
        print(f"有 {len(sink.errors)} 条响应错误，详见 errors.json")
    if not candidates:
        print("尚无可确认的曲线正文，请对照 response_types.json、获准留存路径和人工读数。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="EIP D2 阅页诊断")
    parser.add_argument("--target", default="", help="config/targets.yaml 中的目标名")
    parser.add_argument("--start-url", default="")
    parser.add_argument("--minutes", type=float)
    args = parser.parse_args()
    with (Path(__file__).resolve().parent / "config/targets.yaml").open(encoding="utf-8") as f:
        targets = yaml.safe_load(f)["targets"]
    target = targets[args.target] if args.target else {}
    start = args.start_url or target.get("entry_url") or target.get("base_url")
    run(args.minutes, start, url_patterns=target.get("probe_url_patterns"), base_url=target.get("base_url"))
