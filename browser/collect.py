"""采集普通阅页产生的业务数据，按工单、试验和规则保存证据。"""
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import TimeoutError as BrowserTimeout

from browser.adapter import ApiAdapter
from browser.ctx import BrowserSession
from browser.intercept import ResponseSink, attach
from browser.pages.trial_detail import (TemplateNotReady, load_templates, read_steps,
                                       read_trial_info, save_page_screenshot)
from browser.pages.workorder_list import iter_pages, read_rows
from capture.hover_sampler import sample_points
from reports import write_reports
from rules.engine import load_rules, number, run_all, timestamp
from storage.db import (connect, create_task, insert_anomaly, insert_trial_with_steps_and_curve,
                        insert_workorder, update_task)

ROOT = Path(__file__).resolve().parents[1]


def _order_key(order):
    return str(order.get("workorder_no") or ""), str(order.get("detail_url") or "")


def _list_orders(page, adapter, sink, cfg, directory, limit, timeout_ms, login_wait_ms):
    orders, previous = {}, None
    saved_count = 0

    def read(page_no):
        nonlocal previous, saved_count
        wait = login_wait_ms if page_no == 1 and login_wait_ms else timeout_ms
        deadline = time.monotonic() + wait / 1000
        while True:
            if sink.count != saved_count:
                adapter.replay_manifest(sink.manifest_path, incremental=True)
                saved_count = sink.count
            dom = read_rows(page, cfg) if cfg.get("rows") else []
            # 用同一工单的页面链接补足只返回编号的列表接口。多链接时保留页面行。
            links = {}
            for item in dom:
                links.setdefault(item.get("workorder_no"), []).append(item)
            rows = []
            for item in adapter.last_workorders:
                matches = links.get(item.get("workorder_no"), [])
                if not item.get("detail_url") and matches:
                    rows.extend({**item, **match} for match in matches)
                else:
                    rows.append(item)
            candidates = [rows, dom] if rows else [dom]
            for batch in candidates:
                signature = tuple(_order_key(o) for o in batch)
                if signature and signature != previous:
                    previous = signature
                    shot = save_page_screenshot(page, directory, f"list_{page_no:03d}.png")
                    for row in batch:
                        row["list_url"], row["list_screenshot"] = page.url, str(shot)
                    return batch
            if page_no == 1 and adapter.list_seen and not adapter.last_workorders and not dom:
                return []
            if time.monotonic() >= deadline:
                raise TemplateNotReady("未读到新的工单列表，请确认登录状态、翻页和列表映射")
            page.wait_for_timeout(100)

    def remember(batch):
        for order in batch:
            orders.setdefault(_order_key(order), order)
        return not limit or len(orders) < limit

    iter_pages(page, remember, config=cfg, read_page=read)
    expected = adapter.reported_total
    if expected is not None and len(orders) < (min(limit, expected) if limit else expected):
        raise TemplateNotReady(f"列表报告 {expected} 项，实际取得 {len(orders)} 项，请核对翻页配置")
    return list(orders.values())


def _url(base, detail):
    url = urljoin(base.rstrip("/") + "/", detail)
    target, origin = urlsplit(url), urlsplit(base)
    if target.scheme not in ("http", "https") or (target.scheme, target.netloc) != (origin.scheme, origin.netloc):
        raise TemplateNotReady("详情链接与配置的业务站点不一致")
    return url


def _usable_curve(points):
    return all(any(timestamp(p.get("ts")) and number(p.get(field)) is not None for p in points)
               for field in ("kv", "pc"))


def _slot(adapter, order):
    slots = adapter.staged_trials
    key = order.get("trial_key")
    if key is not None:
        return slots.get(str(key), {})
    if len(slots) > 1:
        raise TemplateNotReady("详情页包含多个试验，请配置工单的 trial_key 后复核")
    return next(iter(slots.values()), {})


def _detail(page, order, cfg, folder, base_url, patterns, timeout_ms):
    sink = ResponseSink(folder, patterns, origin=base_url)
    live = ApiAdapter()
    detach = attach(page, sink)
    try:
        response = page.goto(_url(base_url, order["detail_url"]), wait_until="domcontentloaded")
        if response and not response.ok:
            if response.status in (401, 403):
                raise PermissionError(f"详情页返回 HTTP {response.status}")
            raise TemplateNotReady(f"详情页返回 HTTP {response.status}")
        _url(base_url, page.url)
        deadline = time.monotonic() + timeout_ms / 1000
        parsed = 0
        while time.monotonic() < deadline:
            if sink.count != parsed:
                live.replay_manifest(sink.manifest_path, incremental=True)
                parsed = sink.count
            slot = _slot(live, order)
            if slot.get("trial") and slot.get("steps") and _usable_curve(slot.get("curve", [])):
                break
            if not patterns and read_trial_info(page, cfg):
                break
            page.wait_for_timeout(100)
        if sink.errors:
            raise TemplateNotReady("业务响应未完整读取：" + " / ".join(sink.errors[:3]))
        # 原始响应逐次访问独立归档，离线重放只使用当前详情页的文件。
        live.replay_manifest(sink.manifest_path)
        slot = _slot(live, order)
        trial = dict(slot.get("trial", {}))
        for name, value in read_trial_info(page, cfg).items():
            if name in ("product_no", "test_no") and trial.get(name) and trial[name] != value:
                raise TemplateNotReady("接口和页面的试验标识不一致")
            if not trial.get(name):
                trial[name] = value
        if not trial.get("test_no") and not trial.get("product_no"):
            raise TemplateNotReady("缺少可核对的试验编号或成品编号")
        steps = slot.get("steps") or read_steps(page, cfg)
        curve = list(slot.get("curve", []))
        chart = cfg.get("curve", {})
        if not _usable_curve(curve) and chart.get("tooltip"):
            area = chart["plot_area"]
            visible = sample_points(page, folder / "curve", area["x0"], area["x1"], area["y"],
                                    chart.get("samples", 60), chart.get("dwell_ms", 200), chart)
            # 同时刻按字段优先采用接口观测，保留用于补全该点的截图。
            by_time = {p.get("ts"): p for p in curve}
            for point in visible:
                original = by_time.get(point["ts"])
                if original is None or point.get("unverified") or not point["ts"]:
                    curve.append(point)
                    by_time[point["ts"]] = point
                else:
                    for field in ("kv", "pc"):
                        if number(original.get(field)) is None and point[field] is not None:
                            original[field] = point[field]
                            original["source"] = "api+" + point["source"]
                            original["hover_evidence"] = point["evidence_path"]
        shot = save_page_screenshot(page, folder)
        trial.update(detail_url=page.url, raw_json_path=slot.get("detail_evidence"))
        return dict(trial=trial, steps=steps, curve=curve, screen=shot, folder=folder)
    finally:
        detach()


def _rule_evidence(visit, order, result):
    folder = visit["folder"]
    start, end = timestamp(result["window_start"]), timestamp(result["window_end"])
    selected, unverified = [], []
    files = [visit["screen"]]
    for p in visit["curve"]:
        if p.get("unverified"):
            candidate = p["unverified"]
            candidate_t = timestamp(candidate.get("ts"))
            if not candidate_t or not start or not end or start <= candidate_t <= end:
                path = Path(p["evidence_path"])
                unverified.append({**candidate, "confidence": p["confidence"],
                                   "screenshot": path.relative_to(folder).as_posix()})
                if len(unverified) == 1:
                    files.append(path)
        t = timestamp(p.get("ts"))
        if t is None or start is None or end is None or not start <= t <= end:
            continue
        item = {key: p.get(key) for key in ("ts", "kv", "pc")}
        item["source"] = p.get("source", "api")
        if p.get("confidence") is not None:
            item["confidence"] = p["confidence"]
        for field in ("evidence_path", "hover_evidence"):
            if p.get(field):
                path = Path(p[field])
                item[field] = path.relative_to(folder).as_posix()
                if p["ts"] == result["detail"].get("point_time"):
                    files.append(path)
        selected.append(item)
    trial = visit["trial"]
    if trial.get("raw_json_path"):
        files.append(Path(trial["raw_json_path"]))
    proof = folder / (result["rule_id"] + ".json")
    proof.write_text(json.dumps({
        "workorder_no": order.get("workorder_no"), "product_no": trial.get("product_no"),
        "test_no": trial.get("test_no"), **result, "samples": selected, "unverified_samples": unverified,
        "steps": [(ts, text) for ts, text in visit["steps"] if start and end
                  and timestamp(ts) and start <= timestamp(ts) <= end],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return [str(p) for p in dict.fromkeys([proof, *files])]


def _store(conn, task_id, orders, visits, cfg, list_url):
    ids, rows = {}, []
    for order in orders:
        key = order.get("workorder_no") or _order_key(order)
        if key not in ids:
            ids[key] = insert_workorder(conn, task_id, order, order.get("list_url", list_url), commit=False)
    for order, visit in visits:
        if "error" in visit:
            rows.append({**order, "ok": None, "verdict": "待复核", "severity": "medium",
                         "rule_id": "R-DATA", "rule_name": "试验采集不完整", "rule_version": cfg["version"],
                         "detail": {"note": visit["error"]}, "evidence_items": visit["files"],
                         "review_status": "待复核"})
            continue
        trial, steps, curve = visit["trial"], visit["steps"], visit["curve"]
        trial["workorder_id"] = ids[order.get("workorder_no") or _order_key(order)]
        points = [(str(p.get("ts") or ""), number(p.get("kv")), number(p.get("pc")), p.get("source", "api"),
                   p.get("confidence"), p.get("hover_evidence") or p.get("evidence_path", "")) for p in curve]
        tid = insert_trial_with_steps_and_curve(conn, task_id, trial, steps, points, commit=False)
        for r in run_all(trial, steps, curve, cfg):
            files = _rule_evidence(visit, order, r)
            aid = insert_anomaly(conn, tid, r, files[0], commit=False)
            rows.append({**order, **trial, **r, "trial_id": tid, "anomaly_id": aid,
                         "review_status": "待复核", "evidence": files[0], "evidence_items": files})
    return rows


def collect(base_url, limit=None, headless=True, page_wait_ms=1200, task_note="", task_id=None,
            entry_url=None, profile_dir=None, *, manual_login=False, capture_api=True, templates=None):
    if page_wait_ms < 0:
        raise ValueError("页面等待时间须为非负数")
    if limit is not None and limit < 1:
        raise ValueError("详情数须大于零")
    cfg = load_templates() if templates is None else templates
    rules = load_rules()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory = ROOT / "evidence" / f"collect_{stamp}"
    adapter = ApiAdapter()
    patterns = [ep["url_pattern"] for ep in adapter.endpoints.values()] if capture_api else []
    sink = ResponseSink(directory, patterns, origin=base_url)
    start_url = entry_url or base_url
    conn = connect(ROOT / "eip_agent.db")
    claimed = False
    published = ()
    try:
        if task_id is None:
            task_id = create_task(conn, "collect", {"base_url": base_url, "entry_url": entry_url}, task_note)
        with conn:
            claimed = conn.execute(
                """UPDATE task SET status='running' WHERE id=? AND type='collect'
                   AND status IN ('pending','failed','paused')""", (task_id,)).rowcount == 1
        if not claimed:
            raise ValueError("采集任务不存在、正在运行或已经完成")
        visits = []
        with BrowserSession(headless=headless, profile_dir=profile_dir, record_video=not manual_login,
                            video_dir=directory / "video") as session:
            page = session.page
            detach = attach(page, sink)
            try:
                response = page.goto(start_url, wait_until="domcontentloaded")
                if response and not response.ok:
                    raise RuntimeError(f"列表页返回 HTTP {response.status}")
                orders = _list_orders(page, adapter, sink, cfg["workorder_list"], directory / "page", limit,
                                      max(1000, page_wait_ms), 180000 if manual_login else 0)
                _url(base_url, page.url)
                if sink.errors:
                    raise TemplateNotReady("工单列表响应不完整：" + " / ".join(sink.errors[:3]))
                list_url = page.url
            finally:
                detach()
            next_visit = time.monotonic()
            interval = max(0.0, float(cfg["workorder_list"].get("rate_limit_seconds", 2)))
            for index, order in enumerate(orders[:limit], 1):
                delay = next_visit - time.monotonic()
                if delay > 0:
                    page.wait_for_timeout(delay * 1000)
                next_visit = time.monotonic() + interval
                folder = directory / f"trial_{index:04d}"
                detail = session.context.new_page()
                try:
                    if not order.get("detail_url"):
                        raise TemplateNotReady("此工单没有详情链接")
                    visit = _detail(detail, order, cfg["trial_detail"], folder, base_url, patterns, page_wait_ms)
                except (TemplateNotReady, BrowserTimeout) as exc:
                    files = [order["list_screenshot"]] if order.get("list_screenshot") else []
                    if detail.url != "about:blank" and urlsplit(detail.url).netloc == urlsplit(base_url).netloc:
                        files.append(str(save_page_screenshot(detail, folder)))
                    visit = {"error": str(exc), "files": files}
                finally:
                    detail.close()
                visits.append((order, visit))

        report_dir = ROOT / "reports" / "out"
        with conn:
            rows = _store(conn, task_id, orders, visits, rules, list_url)
            published = write_reports(rows, report_dir / f"异常报表_采集_{stamp}")
            xlsx, html = published
            complete = sum(bool(v.get("trial") and v["steps"] and _usable_curve(v["curve"])) for _, v in visits)
            summary = {"task_id": task_id, "workorders": len(orders), "reported_total": adapter.reported_total, "visited": len(visits),
                       "trials": complete, "identified_trials": sum("trial" in v for _, v in visits),
                       "incomplete": len(visits) - complete,
                       "anomalies": sum(r["ok"] is False for r in rows),
                       "needs_review": sum(r["ok"] is None for r in rows), "excel": str(xlsx),
                       "html": str(html), "evidence": str(directory), "unmatched": adapter.unmatched[:10],
                       "intercept_errors": sink.errors[:10]}
            update_task(conn, task_id, "done", result=summary, commit=False)
        return {**summary, "rows": rows}
    except Exception as exc:
        for path in published:
            path.unlink(missing_ok=True)
        if claimed:
            update_task(conn, task_id, "paused" if isinstance(exc, TemplateNotReady) else "failed", error=str(exc))
        raise
    finally:
        conn.close()
