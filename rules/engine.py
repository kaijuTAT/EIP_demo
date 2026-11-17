"""在步骤定义的时间段内校核实测曲线。ok 为 True、False 或 None（数据不足）。"""
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

import yaml

from ocr.fields import normalize_for_chain

RULES_FILE = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"
VERDICTS = {True: "通过", False: "异常线索", None: "待复核"}


def load_rules():
    with RULES_FILE.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def timestamp(value):
    """EIP 业务时间。显式时区换算为北京时间，无时区的值按页面时间读取。"""
    if not isinstance(value, str) or len(value.strip()) <= 10:
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is not None:
        t = t.astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    return t


def number(value):
    try:
        n = float(value)
        return n if isfinite(n) and not isinstance(value, bool) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _stamp(t):
    return t.isoformat(sep=" ") if t is not None else ""


def _samples(curve):
    # 相同时间的重复读数只计一次。互相矛盾的同一时刻读数交给人工复核。
    samples = {}
    invalid_times = 0
    for point in curve:
        t = timestamp(point.get("ts"))
        if t is None:
            invalid_times += 1
            continue
        values = {key: number(point.get(key)) for key in ("kv", "pc")}
        if t in samples:
            for key, value in values.items():
                if value != samples[t][key]:
                    samples[t][key] = None
        else:
            samples[t] = values
    return [(t, samples[t]) for t in sorted(samples)], invalid_times


def _timeline(steps):
    events = [(timestamp(ts), normalize_for_chain(text)) for ts, text in steps if text]
    return sorted((t, text) for t, text in events if t is not None), len(events)


def _event(events, keyword):
    return next((t for t, text in events if keyword in text), None)


def _between(points, start, end):
    if start is None or end is None or end < start:
        return []
    return [(t, v) for t, v in points if start <= t <= end]


def _result(cfg, rid, ok, detail, start=None, end=None):
    rc = cfg["rules"][rid]
    return dict(rule_id=rid, rule_name=rc["name"], severity=rc["severity"],
                rule_version=cfg["version"], ok=ok, verdict=VERDICTS[ok],
                window_start=_stamp(start), window_end=_stamp(end), detail=detail)


def _coverage(rows, field, minimum=2):
    return len(rows) >= minimum and all(v[field] is not None for _, v in rows)


def _peak(rows, target, rc, start, end):
    if not rows or target is None or not _coverage(rows, "kv"):
        return None, {"note": "缺少等级、阶段边界或有效电压采样"}
    t, v = max(rows, key=lambda row: row[1]["kv"])
    actual = v["kv"]
    detail = {"target_kv": target, "reached_kv": actual, "point_time": _stamp(t)}
    tolerance = rc["tolerance"] * target
    if actual < target - tolerance:
        times = [start, *(t for t, _ in rows), end]
        gap = max((b - a).total_seconds() for a, b in zip(times, times[1:]))
        if gap > rc.get("max_sample_gap_seconds", 5):
            return None, {**detail, "max_gap_s": gap, "note": "未观测到目标峰值，阶段采样覆盖不足"}
    return abs(actual - target) <= tolerance, detail


def _hold(rows, target, seconds, rc, window_start, window_end):
    if target is None or len(rows) < 2:
        return None, {"note": "缺少可定位的平台段或电压采样"}
    gap_limit = float(rc.get("max_sample_gap_seconds", 5))
    best_seconds, best_start, best_end = 0.0, None, None
    start = previous = None
    uncertain = ((rows[0][0] - window_start).total_seconds() > gap_limit or
                 (window_end - rows[-1][0]).total_seconds() > gap_limit)
    for t, v in rows:
        if previous is not None and (t - previous).total_seconds() > gap_limit:
            start = None
            uncertain = True
        previous = t
        kv = v["kv"]
        if kv is None or abs(kv - target) > rc["tolerance"] * target:
            start = None
            uncertain |= kv is None
            continue
        if start is None:
            start = t
        duration = (t - start).total_seconds()
        if best_start is None or duration > best_seconds:
            best_seconds, best_start, best_end = duration, start, t
    detail = {"duration_s": best_seconds, "required_s": seconds,
              "segment_start": _stamp(best_start), "segment_end": _stamp(best_end)}
    if best_seconds >= seconds:
        return True, detail
    if uncertain:
        return None, {**detail, "note": "采样有缺口，无法确认连续保持时长"}
    return False, detail


def _drop(rows, stable, marker, target, rc):
    if marker is None or target is None:
        return None, {"note": "缺少降压完成时刻或电压等级"}
    near = [(t, v["kv"]) for t, v in rows if v["kv"] is not None
            and abs((t - marker).total_seconds()) <= rc.get("marker_slack_seconds", 3)]
    if not near:
        return None, {"note": "降压完成时刻附近没有有效电压"}
    t, kv = min(near, key=lambda item: abs((item[0] - marker).total_seconds()))
    detail = {"target_kv": target, "reached_kv": kv, "point_time": _stamp(t)}
    if abs(kv - target) > target * rc["tolerance"]:
        return False, detail
    excursions = [(t, v["kv"]) for t, v in stable if v["kv"] is not None
                  and abs(v["kv"] - target) > target * rc["tolerance"]]
    if excursions:
        t, kv = max(excursions, key=lambda item: abs(item[1] - target))
        return None, {**detail, "excursion_kv": kv, "point_time": _stamp(t),
                      "note": "已达到降压目标，采值时段出现偏移，持续口径待复核"}
    if not _coverage(stable, "kv"):
        return None, {**detail, "note": "采值时段电压采样不足"}
    return True, detail


def _pd(rows, limit, start, end, max_gap):
    if not _coverage(rows, "pc"):
        return None, {"note": "采集局放值时段数据不足"}
    t, v = max(rows, key=lambda row: row[1]["pc"])
    readings = [v["pc"] for _, v in rows]
    detail = {"limit_pc": limit, "max_pc": v["pc"], "point_time": _stamp(t),
              "steady_avg_pc": round(sum(readings) / len(readings), 2)}
    times = [start, *(t for t, _ in rows), end]
    gap = max((b - a).total_seconds() for a, b in zip(times, times[1:]))
    if gap > max_gap:
        return None, {**detail, "max_gap_s": gap, "note": "采值时段覆盖不足"}
    if max(readings) <= limit:
        return True, detail
    if min(readings) > limit:
        return False, detail
    return None, {**detail, "note": "时段内有局放越限点，需复核持续时间"}


def _background(bg, stable, limit):
    if not _coverage(bg, "pc") or not _coverage(stable, "pc"):
        return None, {"note": "缺少背景或采值时段的有效局放量"}
    background = sum(v["pc"] for _, v in bg) / len(bg)
    steady = sum(v["pc"] for _, v in stable) / len(stable)
    return background <= limit and steady >= background * 0.9, {
        "background_pc": round(background, 2), "steady_pc": round(steady, 2)}


def _shape(points, invalid_times, rc):
    if len(points) < 2:
        return None, {"note": "有效时刻不足"}
    stuck = run = 0
    previous_t = previous_kv = None
    gap = 0.0
    gap_at = stuck_at = None
    for t, v in points:
        if previous_t is not None:
            delta = (t - previous_t).total_seconds()
            if delta > gap:
                gap, gap_at = delta, t
        kv = v["kv"]
        run = run + 1 if kv is not None and kv == previous_kv else int(kv is not None)
        if run > stuck:
            stuck, stuck_at = run, t
        previous_t, previous_kv = t, kv
    detail = {"max_stuck_points": stuck, "max_gap_s": gap}
    if gap > rc["gap_seconds"] or stuck > rc["stuck_max_points"]:
        return False, {**detail, "point_time": _stamp(gap_at if gap > rc["gap_seconds"] else stuck_at)}
    if invalid_times or not _coverage(points, "kv"):
        return None, {**detail, "note": "存在无效时间或电压"}
    return True, detail


def run_all(trial, steps, curve, rules_cfg=None):
    cfg = rules_cfg or load_rules()
    rules = cfg["rules"]
    vc = cfg["voltage_classes"].get(str(trial.get("voltage_class", "")).strip())
    points, invalid_times = _samples(curve)
    events, total_events = _timeline(steps)
    anchors = {k: _event(events, keyword) for k, keyword in cfg["phase_markers"].items()}
    begin, drop = anchors["pd_start"], anchors["drop"]
    stable, end = anchors["read_start"], anchors["read_end"]
    withstand, finish = anchors["withstand"], anchors["finished"]
    bg_start, bg_end = anchors["background_start"], anchors["background_end"]
    rising = _between(points, begin, drop)
    steady = _between(points, stable, end)
    pd_target = vc["u0"] * vc["mult_pd"] if vc else None
    results = []

    def add(rid, outcome, start=None, stop=None):
        if rules[rid].get("enabled", True):
            ok, detail = outcome
            if invalid_times and (ok is True and rid != "R-PD-01" or rid == "R-PD-03" and ok is False):
                ok, detail = None, {**detail, "note": "存在无法定位时段的曲线采样"}
            results.append(_result(cfg, rid, ok, detail, start, stop))

    text = " ".join(s for _, s in events)
    missing = []
    pos = 0
    for keyword in cfg["step_chain_keywords"]:
        keyword = normalize_for_chain(keyword)
        i = text.find(keyword, pos)
        if i < 0:
            missing.append(keyword)
        else:
            pos = i + len(keyword)
    chain_ok = not missing if events and len(events) == total_events else None
    if missing and finish is None and trial.get("test_status") not in ("完成", "已完成"):
        chain_ok = None
    add("R-PD-01", (chain_ok, {"missing_keywords": missing}), events[0][0] if events else None, finish)
    add("R-PD-02", _peak(rising, pd_target, rules["R-PD-02"], begin, drop), begin, drop)
    add("R-PD-03", _hold(rising, pd_target, vc["hold_seconds"] if vc else None, rules["R-PD-03"], begin, drop), begin, drop)
    drop_target = vc["u0"] * vc["mult_hold"] if vc else None
    add("R-PD-04", _drop(_between(points, drop, end), steady, stable, drop_target, rules["R-PD-04"]), drop, end)
    w_target = vc["u0"] * vc["mult_withstand"] if vc else None
    add("R-PD-05", _peak(_between(points, withstand, finish), w_target, rules["R-PD-05"], withstand, finish), withstand, finish)
    pc = bg = (None, {"note": "缺少该等级对应的局放规则"})
    if vc:
        pc = _pd(steady, vc.get("pc_limit", rules["R-PD-06"]["pc_limit"]), stable, end,
                 rules["R-PD-06"].get("max_sample_gap_seconds", 5))
        bg = _background(_between(points, bg_start, bg_end), steady,
                         vc.get("background_pc_limit", rules["R-PD-07"]["pc_limit"]))
    add("R-PD-06", pc, stable, end)
    add("R-PD-07", bg, bg_start, bg_end)
    add("R-PD-08", _shape(points, invalid_times, rules["R-PD-08"]),
        points[0][0] if points else None, points[-1][0] if points else None)
    return results
