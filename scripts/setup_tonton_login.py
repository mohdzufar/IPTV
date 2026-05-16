#!/usr/bin/env python3
import io
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROFILE_DIR = Path(
    r"C:\Users\zufar\Downloads\IPTV_Project\GitHub_Runner_IPTV\actions-runner\auth\tonton-profile"
)
START_URL = "https://watch.tonton.com.my/live/tv9"
CHECK_URLS = [
    "https://watch.tonton.com.my/live/tv3",
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
    print("=" * 60)
    print("TONTON persistent profile setup")
    print(f"Profile folder: {PROFILE_DIR}")
    print("=" * 60)

    if PROFILE_DIR.exists():
        answer = input("Existing Tonton profile found. Replace it? (y/N): ").strip().lower()
        if answer == "y":
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    stealth = Stealth(
        navigator_user_agent_override=USER_AGENT,
        navigator_platform_override="Win32",
        navigator_languages_override=("en-US", "en"),
    )

    with stealth.use_sync(sync_playwright()) as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,
            slow_mo=150,
            args=["--disable-blink-features=AutomationControlled"],
            user_agent=USER_AGENT,
            extra_http_headers={"Referer": REFERER},
            locale="en-US",
            timezone_id="Asia/Kuala_Lumpur",
            viewport={"width": 1440, "height": 900},
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(START_URL, wait_until="domcontentloaded", timeout=45000)

        print("=" * 60)
        print("A Chromium window is now open with the Tonton profile.")
        print("1. Log in to your Tonton account.")
        print("2. Open and confirm these pages do not redirect to /login:")
        for url in CHECK_URLS:
            print(f"   - {url}")
        print("3. Return here and press Enter.")
        print("=" * 60)
        input("Press Enter after login and channel checks are complete... ")

        failed = []
        for url in CHECK_URLS:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)
            print(f"Checked: {url}")
            print(f"Final URL: {page.url}")
            if is_login_url(page.url):
                failed.append(url)

        if failed:
            print("\nProfile is not fully authenticated for these channels:")
            for url in failed:
                print(f"- {url}")
            print("\nDo not use this profile yet. Re-run setup and confirm access first.")
        else:
            print(f"\nTONTON persistent profile is ready at: {PROFILE_DIR}")

        context.close()


if __name__ == "__main__":
    main()
