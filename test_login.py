"""Playwright login diagnostic — tests the auto-login + token capture flow."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

config = json.loads(Path("config.json").read_text())
active = config["active_profile"]
profile = config["profiles"][active]
email = profile["email"]
password = profile["password"]

captured = []

def on_request(req):
    auth = req.headers.get("authorization", "")
    if auth.startswith("Bearer ") and not captured:
        captured.append(auth[7:])
        print(f"  *** Bearer token captured! ***")

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=False, slow_mo=200)
    ctx = browser.new_context()
    page = ctx.new_page()
    page.on("request", on_request)

    print("Step 1: navigate to login page...")
    page.goto("https://members.onepeloton.com/login", wait_until="domcontentloaded", timeout=30_000)
    print(f"  URL: {page.url}")

    print("Step 2: waiting for auto-login or form...")
    try:
        page.wait_for_url(
            lambda url: "members.onepeloton.com" in url and "/login" not in url,
            timeout=10_000,
        )
        print(f"  Auto-login redirect detected. URL: {page.url}")
    except PWTimeout:
        print(f"  No auto-login — filling form. URL: {page.url}")
        page.locator('input[name="usernameOrEmail"]').fill(email)
        page.locator('input[name="password"]').fill(password)
        page.locator('button[type="submit"]').first.click()
        page.wait_for_url(
            lambda url: "members.onepeloton.com" in url and "/login" not in url,
            timeout=30_000,
        )
        print(f"  Form login succeeded. URL: {page.url}")

    print("Step 3: waiting for app to finish loading...")
    page.wait_for_load_state("networkidle", timeout=30_000)
    print(f"  URL: {page.url}")
    print(f"  Tokens captured so far: {len(captured)}")

    if not captured:
        print("Step 4: navigating to profile to trigger API calls...")
        page.goto("https://members.onepeloton.com/profile", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        print(f"  Tokens captured: {len(captured)}")

    if captured:
        print(f"\nSuccess! Token starts with: {captured[0][:40]}...")
    else:
        print("\nFailed to capture token.")

    input("Press Enter to close...")
    browser.close()
