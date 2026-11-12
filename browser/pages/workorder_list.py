"""生产试验列表页解析与安全翻页。

纪律：翻页间隔 >= rate_limit_seconds（config），页数上限 max_pages。
"""
import time

from browser.pages.trial_detail import TemplateNotReady, load_templates


def read_rows(page) -> list:
    """读当前页全部行 → [ {列名: 值} ]。"""
    tpl = load_templates()
    lt = tpl.get("workorder_list", {})
    rows_sel = lt.get("rows", "")
    if not rows_sel:
        raise TemplateNotReady("workorder_list.rows 未回填，请先完成 D2 验证")
    columns = lt.get("columns", {})
    out = []
    for row in page.locator(rows_sel).all():
        rec = {}
        for col, sel in columns.items():
            try:
                rec[col] = row.locator(sel).first.inner_text(timeout=2000).strip() if sel else None
            except Exception:
                rec[col] = None
        out.append(rec)
    return out


def iter_pages(page, on_page=None):
    """安全翻页迭代。on_page(rows) 每页回调；到尾页或达上限停止。"""
    tpl = load_templates()
    lt = tpl.get("workorder_list", {})
    next_sel = lt.get("pager", {}).get("next_button", "")
    if not next_sel:
        raise TemplateNotReady("workorder_list.pager.next_button 未回填")
    max_pages = int(lt.get("pager", {}).get("max_pages", 200))
    rate = float(lt.get("rate_limit_seconds", 2))

    for page_no in range(1, max_pages + 1):
        rows = read_rows(page)
        if on_page:
            on_page(rows)
        nxt = page.locator(next_sel)
        if not nxt.count() or not nxt.first.is_enabled():
            break
        nxt.first.click()
        time.sleep(rate)  # 纪律红线：限速
    return page_no
