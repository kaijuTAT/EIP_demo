"""Playwright 浏览器会话管理。

首版纪律：人工登录，脚本不碰凭据。用持久化用户目录（persistent context）
保存登录态，第一次人工登录后，后续任务复用同一目录即可免登录操作。
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

PROFILE_DIR = Path(__file__).resolve().parents[1] / "evidence" / "browser_profile"


class BrowserSession:
    """同步版会话。with BrowserSession() as s: s.page.goto(...)"""

    def __init__(self, headless: bool = False, record_video: bool = False,
                 profile_dir: Path = None):
        self.headless = headless
        self.record_video = record_video
        self.profile_dir = profile_dir or PROFILE_DIR
        self._pw = None
        self.context = None
        self.page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        kwargs = dict(
            user_data_dir=str(self.profile_dir),
            headless=self.headless,
            viewport={"width": 1600, "height": 900},
        )
        if self.record_video:
            video_dir = Path(__file__).resolve().parents[1] / "evidence" / "videos"
            video_dir.mkdir(parents=True, exist_ok=True)
            kwargs["record_video_dir"] = str(video_dir)
        self.context = self._pw.chromium.launch_persistent_context(**kwargs)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def __exit__(self, *exc):
        if self.context:
            self.context.close()
        if self._pw:
            self._pw.stop()
        return False
