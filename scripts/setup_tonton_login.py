#!/usr/bin/env python3
import io
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

STATE_FILE = Path(
    r"C:\Users\zufar\Downloads\IPTV_Project\GitHub_Runner_IPTV\actions-runner\auth\tonton-state.json"
)
START_URL = "https://watch.tonton.com.my/live/tv9"
CHECK_URLS = [
    "https://watch.tonton.com.my/live/tv9",
    "https://watch.tonton.com.my/live/ntv7",
    "https://watch.tonton.com.my/live/ds",
]
REFERER = "https://watch.tonton.com.my/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def is_login_url(url: str) -> bool:
    lower = url.lower()
    return any(x in lower for x in ("login", "signin", "sign-in", "auth"))


def main():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    stealth = Stealth(
        navigator_user_agent_override=USER_AGENT,
        navigator_platform_override="Win32",
        navigator_languages_override=("en-US", "en"),
    )

    with stealth.use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=150,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": REFERER},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            viewport={"width": 1440, "height": 900},
        )

        page = context.new_page()
        page.goto(START_URL, wait_until="domcontentloaded", timeout=45000)

        print("=" * 60)
        print("TONTON login window opened.")
        print("1. Log in to your Tonton account.")
        print("2. After login, make sure TV9 opens without redirecting to /login.")
        print("3. Then press Enter here to verify and save the session.")
        print("=" * 60)
        input("Press Enter after login is complete... ")

        failed = []
        for url in CHECK_URLS:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            print(f"Checked: {url}")
            print(f"Final URL: {page.url}")
            if is_login_url(page.url):
                failed.append(url)

        if failed:
            print("\nSession was not fully authenticated for these protected channels:")
            for url in failed:
                print(f"- {url}")
            print("\nDo not use this state file yet. Log in again and confirm access first.")
        else:
            context.storage_state(path=str(STATE_FILE))
            print(f"\nSaved Tonton login state to: {STATE_FILE}")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
