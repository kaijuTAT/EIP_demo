"""T1 接口版采集入口。

本地联调（Mock EIP）：
    python run_collect.py --base-url http://127.0.0.1:8777 --headless

真实 EIP（D2 验证后，改好 config/api_map.yaml 再用）：
    python run_collect.py --base-url https://<eip-host> --limit 20 --no-headless
    （人工先在弹出的浏览器里登录；脚本只接管已登录会话，只读不写）
"""
import argparse
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser.collect import collect  # noqa: E402


ROOT = Path(__file__).resolve().parent
_MOCK_SERVER = None


def load_target(name: str) -> dict:
    path = ROOT / "config" / "targets.yaml"
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("targets", {}).get(name, {})


def ensure_mock_server(base_url: str) -> None:
    """选择 Mock 目标时自动拉起本地仿真服务。"""
    global _MOCK_SERVER
    if base_url.rstrip("/") != "http://127.0.0.1:8777":
        return
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/", timeout=0.3):
            return
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    from werkzeug.serving import make_server
    from mock_eip.server import app as mock_app
    _MOCK_SERVER = make_server("127.0.0.1", 8777, mock_app)
    threading.Thread(target=_MOCK_SERVER.serve_forever,
                     name="mock-eip", daemon=True).start()


def main():
    ap = argparse.ArgumentParser(description="EIP T1 接口版采集")
    ap.add_argument("--target", default="", help="config/targets.yaml 中的目标名")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--entry-url", default="", help="列表页或 SPA 入口 URL")
    ap.add_argument("--limit", type=int, default=None, help="最多进入的详情页数")
    ap.add_argument("--page-wait-ms", type=int, default=1200)
    ap.add_argument("--profile-dir", default="", help="浏览器登录态目录，默认 evidence/browser_profile")
    ap.add_argument("--headless", dest="headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false",
                    help="显示浏览器窗口（真实 EIP 人工登录场景用）")
    args = ap.parse_args()

    target = load_target(args.target) if args.target else {}
    base_url = args.base_url or target.get("base_url", "")
    entry_url = args.entry_url or target.get("entry_url", "") or None
    if not base_url:
        ap.error("请提供 --base-url，或在 config/targets.yaml 配置 --target")
    if args.target == "mock":
        ensure_mock_server(base_url)
    profile_dir = Path(args.profile_dir) if args.profile_dir else None
    print(f"采集目标：{base_url} 入口：{entry_url or '(根路径)'} "
          f"headless={args.headless} limit={args.limit}")
    summary = collect(args.base_url or base_url, limit=args.limit, headless=args.headless,
                      page_wait_ms=args.page_wait_ms, entry_url=entry_url,
                      profile_dir=profile_dir)

    print(f"\n工单 {summary['workorders']} 条 | 完整试验 {summary['trials']} 条 | "
          f"异常判定 {summary['anomalies']} 条")
    for row in summary["rows"]:
        if not row["ok"]:
            print(f"  ✗ {row['rule_id']} {row['rule_name']}  {row['product_no']}  {row['detail']}")
    if summary["unmatched"]:
        print(f"\n未匹配接口（供补充 api_map）：{summary['unmatched']}")
    print(f"\n报表：\n  {summary['excel']}\n  {summary['html']}\n证据目录：{summary['evidence']}")


if __name__ == "__main__":
    main()
