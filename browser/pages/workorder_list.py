"""按页面模板读取可见工单，沿已配置的下一页控件翻页。"""
from browser.pages.trial_detail import TemplateNotReady, load_templates


def read_rows(page, config=None):
    config = config if config is not None else load_templates()["workorder_list"]
    selector = config.get("rows")
    if not selector:
        raise TemplateNotReady("workorder_list.rows 未配置")
    orders = []
    for row in page.locator(selector).all():
        rec = {}
        for name, cell in config.get("columns", {}).items():
            if cell:
                field = row.locator(cell).first
                rec[name] = field.inner_text().strip() if field.count() else None
        link_selector = config.get("detail_link")
        if link_selector:
            link = row.locator(link_selector).first
            if link.count():
                rec["detail_url"] = link.evaluate("a => a.href") if link.get_attribute("href") else None
        if any(rec.values()):
            orders.append(rec)
    return orders


def iter_pages(page, on_page=None, *, config=None, read_page=None):
    config = config if config is not None else load_templates()["workorder_list"]
    pager = config.get("pager", {})
    next_selector = pager.get("next_button")
    maximum = max(1, int(pager.get("max_pages", 200)))
    for page_no in range(1, maximum + 1):
        rows = read_page(page_no) if read_page else read_rows(page, config)
        if on_page and on_page(rows) is False:
            break
        if not next_selector:
            break
        nxt = page.locator(next_selector).first
        if not nxt.count() or not nxt.is_visible() or not nxt.is_enabled():
            break
        if page_no == maximum:
            raise TemplateNotReady("工单列表达到翻页上限，仍有下一页")
        nxt.click()
        page.wait_for_timeout(1000 * float(config.get("rate_limit_seconds", 2)))
    return page_no
