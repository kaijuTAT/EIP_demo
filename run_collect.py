"""T1 接口版采集入口。

本地联调（Mock EIP）：
    python run_collect.py --base-url http://127.0.0.1:8777 --headless

真实 EIP（D2 验证后，改好 config/api_map.yaml 再用）：
    python run_collect.py --base-url https://<eip-host> --limit 20 --no-headless
    （人工先在弹出的浏览器里登录；脚本只接管已登录会话，只读不写）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser.collect import collect  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="EIP T1 接口版采集")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--limit", type=int, default=None, help="最多进入的详情页数")
    ap.add_argument("--headless", dest="headless", action="store_true", default=True)
    ap.add_argument("--no-headless", dest="headless", action="store_false",
                    help="显示浏览器窗口（真实 EIP 人工登录场景用）")
    args = ap.parse_args()

    print(f"采集目标：{args.base_url}  headless={args.headless}  limit={args.limit}")
    summary = collect(args.base_url, limit=args.limit, headless=args.headless)

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
