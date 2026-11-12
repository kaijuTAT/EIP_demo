"""D2 流量验证脚本（试点第一步，关键决策点）。

用法（在外网 Windows 机上）：
    python run_probe.py                 # 打开浏览器，人工登录 EIP
    #   登录后正常翻 5~10 个局放试验详情页（脚本在后台记录所有 JSON 响应）
    #   回到终端按回车结束，自动输出候选数据接口清单
    python run_probe.py --minutes 15    # 或定时 15 分钟自动结束

产出：evidence/probe_<时间戳>/
    raw/resp_*.json   全部命中的 JSON 响应原文
    manifest.jsonl    每条记录 URL/状态/字节数/关键词提示
    shots/page_*.png  每 30 秒整页截图（对照用）
结论怎么下：看打印的"候选曲线接口"——
    出现 kv/pc/voltage/discharge/曲线 等关键词的大响应 → T1 接口通道可行
"""
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from browser.ctx import BrowserSession
from browser.intercept import ResponseSink, attach

EVIDENCE_ROOT = Path(__file__).resolve().parent / "evidence"
CURVE_HINTS = ("kv", "pc", "voltage", "discharge", "partial", "curve", "局放", "电压", "曲线")


def run(minutes: float = None):
    out_dir = EVIDENCE_ROOT / f"probe_{datetime.now():%Y%m%d_%H%M%S}"
    sink = ResponseSink(out_dir)
    stop_flag = threading.Event()

    with BrowserSession(headless=False) as s:
        page = s.page
        detach = attach(page, sink)

        def screenshot_loop():
            i = 0
            shots = out_dir / "shots"
            while not stop_flag.is_set():
                try:
                    shots.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(shots / f"page_{i:03d}.png"), full_page=False)
                except Exception:
                    pass
                i += 1
                stop_flag.wait(30)

        shooter = threading.Thread(target=screenshot_loop, daemon=True)
        shooter.start()

        print("=" * 60)
        print("浏览器已打开。请人工登录 EIP，然后翻看 5~10 个局放试验详情页。")
        print("脚本在后台记录所有 JSON 响应。完成后回到本窗口按回车结束。")
        print("=" * 60)
        try:
            if minutes:
                time.sleep(minutes * 60)
            else:
                input("按回车结束采集 > ")
        except KeyboardInterrupt:
            pass
        finally:
            stop_flag.set()
            detach()

    summarize(out_dir, sink)


def summarize(out_dir: Path, sink: ResponseSink):
    print("\n" + "=" * 60)
    print(f"采集完成：{sink.count} 条 JSON 响应 → {out_dir}")
    print("=" * 60)
    candidates = []
    manifest = out_dir / "manifest.jsonl"
    if manifest.exists():
        for line in manifest.read_text(encoding="utf-8").splitlines():
            import json
            rec = json.loads(line)
            curve_kw = [k for k in rec.get("hint", []) if k in CURVE_HINTS]
            if curve_kw and rec["bytes"] > 500:
                candidates.append((rec["bytes"], rec["url"], curve_kw, rec["file"]))
    if candidates:
        candidates.sort(reverse=True)
        print("\n★ 候选数据接口（含曲线/电压/局放关键词，按体积排序，前 15 条）：")
        for size, url, kw, f in candidates[:15]:
            print(f"  [{size:>8} B] {url[:100]}")
            print(f"      关键词: {kw}  证据: {f}")
        print("\n结论倾向：T1 接口通道可行——把上述 URL 模式回填到采集配置即可。")
    else:
        print("\n未发现明显的曲线数据接口。结论倾向：走 T3 悬浮框采样 + OCR 通道。")
    print(f"\n下一步：人工抽查 {out_dir / 'raw'} 下的大体积 JSON，确认曲线点位字段名，")
    print("然后回填 config/page_templates.yaml 的 selector。")


if __name__ == "__main__":
    mins = None
    if "--minutes" in sys.argv:
        mins = float(sys.argv[sys.argv.index("--minutes") + 1])
    run(mins)
