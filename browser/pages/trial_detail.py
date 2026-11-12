"""试验感知详情页（成盘局放耐压试验）解析。

定位器来自 config/page_templates.yaml。selector 未回填时抛 TemplateNotReady，
采集暂停转人工——绝不在模板缺失时猜字段写脏数据。
"""
from pathlib import Path

import yaml

from ocr.fields import parse_step_lines

TEMPLATES = Path(__file__).resolve().parents[2] / "config" / "page_templates.yaml"


class TemplateNotReady(Exception):
    """页面模板未回填（等 D2 验证）。调用方应暂停任务并提示人工。"""


def load_templates() -> dict:
    with open(TEMPLATES, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sel(tpl: dict, field: str) -> str:
    s = tpl["trial_detail"]["fields"].get(field, {}).get("selector", "")
    if not s:
        raise TemplateNotReady(f"trial_detail.fields.{field} 未回填，请先完成 D2 验证")
    return s


def read_trial_info(page) -> dict:
    """DOM 直读试验信息栏字段（T2 通道）。"""
    tpl = load_templates()
    out = {}
    for field in tpl["trial_detail"]["fields"]:
        try:
            out[field] = page.locator(_sel(tpl, field)).first.inner_text(timeout=3000).strip()
        except TemplateNotReady:
            raise
        except Exception:
            out[field] = None
    return out


def read_steps(page) -> list:
    """步骤时间线：DOM 行内自带时间戳，直接解析成 [(occurred_at, text)]。"""
    tpl = load_templates()
    sel = tpl["trial_detail"]["steps"].get("selector", "")
    if not sel:
        raise TemplateNotReady("trial_detail.steps.selector 未回填，请先完成 D2 验证")
    lines = [l.strip() for l in page.locator(sel).all_inner_texts() if l.strip()]
    return parse_step_lines(lines)


def save_page_screenshot(page, evidence_dir: Path, name: str = "detail.png") -> Path:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    p = evidence_dir / name
    page.screenshot(path=str(p), full_page=True)
    return p
