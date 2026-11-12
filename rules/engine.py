"""规则引擎：数值判定只走规则，不走大模型。

输入：trial 字段、步骤时间线、曲线点序列
输出：[{rule_id, rule_name, severity, ok, detail}]，ok=False 即异常记录
规则参数全部来自 config/rules.yaml（业务可改、版本化）。
"""
from datetime import datetime
from pathlib import Path

import yaml

RULES_FILE = Path(__file__).resolve().parents[1] / "config" / "rules.yaml"


def load_rules() -> dict:
    with open(RULES_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _targets(voltage_class: str, cfg: dict) -> dict:
    vc = cfg["voltage_classes"].get(str(voltage_class)) or cfg["voltage_classes"]["26/35"]
    return {
        "pd": vc["u0"] * vc["mult_pd"],            # 52.0
        "after_hold": vc["u0"] * vc["mult_hold"],  # 44.98
        "withstand": vc["u0"] * vc["mult_withstand"],  # 117.0
        "hold_seconds": vc["hold_seconds"],
        "vc_cfg": vc,
    }


def _ts(s):
    """容错解析时间戳：OCR 噪声/非法格式返回 None 而不是崩。"""
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _span_seconds(points):
    """点序列首尾时间差（秒）；可解析时间戳不足 2 个返回 0。"""
    ts = [t for t in (_ts(p.get("ts")) for p in points) if t]
    if len(ts) < 2:
        return 0.0
    return (ts[-1] - ts[0]).total_seconds()


def _longest_plateau(curve, target, tol):
    """距 target 容差内的最长连续段（按点数）。curve 已按时间排序。"""
    best = cur = 0
    for p in curve:
        kv = p.get("kv")
        if kv is not None and abs(kv - target) / target <= tol:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def check_step_chain(steps, cfg):
    """R-PD-01：步骤关键词按顺序齐全（归一化后匹配，容忍 OCR 噪声）。
    顺序用"从当前位置向后找"的游标法，避免后面的关键词在前文提前命中
    （如'完成'出现在'背景值采集完成'里）。"""
    from ocr.fields import normalize_for_chain
    text = normalize_for_chain(" ".join(t for _, t in steps))
    missing, order_break = [], []
    pos = 0
    for kw in cfg["step_chain_keywords"]:
        nk = normalize_for_chain(kw)
        i = text.find(nk, pos)
        if i < 0:
            missing.append(kw)
        else:
            pos = i + len(nk)
    ok = not missing
    return ok, {"missing_keywords": missing}


def _phase_stats(curve, targets):
    """按试验逻辑分段取极值：
    pd_peak   = 耐压上升（>0.6倍耐压目标）之前的最大电压（局放升压段峰值）
    valley    = 首次到达 2U0 平台之后的最低电压（降压段谷值）
    w_peak    = 全程最大电压（耐压峰值，耐压是全程最高）
    """
    pts = [(p.get("ts"), p["kv"]) for p in curve if p.get("kv") is not None]
    if not pts:
        return None, None, None
    w_rise = 0.6 * targets["withstand"]
    idx_w = next((i for i, (_, kv) in enumerate(pts) if kv > w_rise), len(pts))
    pd_peak = max(kv for _, kv in pts[:idx_w])
    idx_p = next((i for i, (_, kv) in enumerate(pts)
                  if abs(kv - targets["pd"]) / targets["pd"] <= 0.05), None)
    valley = (min(kv for i, (_, kv) in enumerate(pts) if i > idx_p)
              if idx_p is not None else None)
    w_peak = max(kv for _, kv in pts)
    return pd_peak, valley, w_peak


def check_voltage_targets(curve, targets, rules_cfg):
    """R-PD-02/04/05：升压、降压、耐压三阶段实测极值与目标偏差。"""
    pd_peak, valley, w_peak = _phase_stats(curve, targets)
    if pd_peak is None:
        return []
    out = []
    for rid, target, reached in (("R-PD-02", targets["pd"], pd_peak),
                                 ("R-PD-04", targets["after_hold"], valley),
                                 ("R-PD-05", targets["withstand"], w_peak)):
        rc = rules_cfg["rules"][rid]
        if not rc.get("enabled", True) or reached is None:
            if rc.get("enabled", True):
                out.append((rid, rc, False,
                            {"target_kv": target, "reached_kv": None, "note": "曲线数据缺失"}))
            continue
        tol = float(rc["tolerance"])
        dev = (reached - target) / target
        out.append((rid, rc, abs(dev) <= tol,
                    {"target_kv": target, "reached_kv": reached, "deviation": round(dev, 4)}))
    return out


def check_hold_duration(curve, targets, rules_cfg):
    """R-PD-03：2U0 平台维持时长。采样点带时间戳时按时间差算，否则按点数*间隔估。"""
    rc = rules_cfg["rules"]["R-PD-03"]
    if not rc.get("enabled", True):
        return None
    tol = float(rc["tolerance"])
    on_pl = [p for p in curve if p.get("kv") is not None
             and abs(p["kv"] - targets["pd"]) / targets["pd"] <= tol]
    dur = _span_seconds(on_pl)
    min_s = float(targets["hold_seconds"])
    return ("R-PD-03", rc, dur >= min_s,
            {"duration_s": dur, "required_s": min_s, "points_on_plateau": len(on_pl)})


def check_pd_limit(curve, rules_cfg, phase="steady"):
    """R-PD-06：稳态局放超限。稳态取采样后半段均值（试点近似）。"""
    rc = rules_cfg["rules"]["R-PD-06"]
    if not rc.get("enabled", True):
        return None
    pcs = [p["pc"] for p in curve if p.get("pc") is not None]
    if not pcs:
        return ("R-PD-06", rc, True, {"note": "无局放数据，跳过"})
    steady = pcs[len(pcs) // 2:]
    avg = sum(steady) / len(steady)
    limit = float(rc["pc_limit"])
    return ("R-PD-06", rc, avg <= limit,
            {"steady_avg_pc": round(avg, 2), "limit_pc": limit, "max_pc": max(pcs)})


def check_background(curve, rules_cfg):
    """R-PD-07：背景局放值合理性。好电缆稳态≈背景属正常；
    可疑条件：背景噪声底超限，或稳态明显低于背景（物理上不可能，数据可疑）。"""
    rc = rules_cfg["rules"]["R-PD-07"]
    if not rc.get("enabled", True):
        return None
    pcs = [p["pc"] for p in curve if p.get("pc") is not None]
    if len(pcs) < 4:
        return ("R-PD-07", rc, True, {"note": "点数不足，跳过"})
    bg = sum(pcs[: len(pcs) // 2]) / (len(pcs) // 2)
    steady = sum(pcs[len(pcs) // 2:]) / (len(pcs) - len(pcs) // 2)
    limit = float(rc["pc_limit"])
    ok = bg <= limit and steady >= bg * 0.9
    return ("R-PD-07", rc, ok,
            {"background_pc": round(bg, 2), "steady_pc": round(steady, 2)})


def check_curve_shape(curve, rules_cfg):
    """R-PD-08：数据死点（连续相同读数）与采样缺口。"""
    rc = rules_cfg["rules"]["R-PD-08"]
    if not rc.get("enabled", True):
        return None
    stuck_max, gap_max = int(rc["stuck_max_points"]), float(rc["gap_seconds"])
    stuck = cur = 1
    prev = None
    for p in curve:
        kv = p.get("kv")
        if kv is not None and prev is not None and kv == prev:
            cur += 1
            stuck = max(stuck, cur)
        else:
            cur = 1
        prev = kv
    biggest_gap = 0.0
    tss = [t for t in (_ts(p.get("ts")) for p in curve) if t]
    for a, b in zip(tss, tss[1:]):
        biggest_gap = max(biggest_gap, (b - a).total_seconds())
    ok = stuck <= stuck_max and biggest_gap <= gap_max
    return ("R-PD-08", rc, ok,
            {"max_stuck_points": stuck, "max_gap_s": biggest_gap})


def run_all(trial: dict, steps: list, curve: list, rules_cfg: dict = None) -> list:
    """主入口。trial 需含 voltage_class；steps [(occ,text)]；curve [{ts,kv,pc}]。"""
    cfg = rules_cfg or load_rules()
    targets = _targets(trial.get("voltage_class"), cfg)
    results = []

    rc01 = cfg["rules"]["R-PD-01"]
    if rc01.get("enabled", True):
        ok, detail = check_step_chain(steps, cfg)
        results.append(dict(rule_id="R-PD-01", rule_name=rc01["name"],
                            severity=rc01["severity"], ok=ok, detail=detail))

    for rid, rc, ok, detail in check_voltage_targets(curve, targets, cfg):
        results.append(dict(rule_id=rid, rule_name=rc["name"],
                            severity=rc["severity"], ok=ok, detail=detail))

    for checker in (check_hold_duration, lambda c, t, r: check_pd_limit(c, r),
                    lambda c, t, r: check_background(c, r),
                    lambda c, t, r: check_curve_shape(c, r)):
        r = checker(curve, targets, cfg)
        if r is not None:
            rid, rc, ok, detail = r
            results.append(dict(rule_id=rid, rule_name=rc["name"],
                                severity=rc["severity"], ok=ok, detail=detail))
    return results
