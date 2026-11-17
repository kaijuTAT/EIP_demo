"""读取当前试验详情中已配置的可见字段与步骤。"""
from pathlib import Path

import yaml

from ocr.fields import parse_step_lines

TEMPLATES = Path(__file__).resolve().parents[2] / "config" / "page_templates.yaml"


class TemplateNotReady(Exception):
    """当前页面或业务定位器无法确认。"""


def load_templates():
    with TEMPLATES.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_trial_info(page, config=None):
    config = config if config is not None else load_templates()["trial_detail"]
    out = {}
    for name, field in config.get("fields", {}).items():
        if not field.get("selector"):
            continue
        element = page.locator(field["selector"]).first
        if element.count() and element.is_visible():
            attr = field.get("attr", "text")
            value = element.inner_text() if attr == "text" else element.get_attribute(attr)
            if value:
                out[name] = value.strip()
    return out


def read_steps(page, config=None):
    config = config if config is not None else load_templates()["trial_detail"]
    selector = config.get("steps", {}).get("selector")
    if not selector:
        return []
    return parse_step_lines(page.locator(selector).all_inner_texts())


def save_page_screenshot(page, evidence_dir: Path, name="detail.png"):
    evidence_dir.mkdir(parents=True, exist_ok=True)
    path = evidence_dir / name
    page.screenshot(path=str(path), full_page=True)
    return path
