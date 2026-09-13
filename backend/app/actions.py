import time
import os
from playwright.sync_api import sync_playwright

SCREENSHOT_DIR = "./reports/screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Common label/name/placeholder fragments mapped to the generic values we fill.
# Extend this map rather than hardcoding a specific site's field names.
FIELD_ALIASES = {
    "name": ["name", "full name", "first name", "your name"],
    "email": ["email", "e-mail", "your email"],
}


def _matches(field_attr: str, aliases: list[str]) -> bool:
    field_attr = (field_attr or "").lower()
    return any(alias in field_attr for alias in aliases)


def fill_and_submit_form(url: str, field_values: dict) -> dict:
    """Opens a real page, autofills a safe/reversible form (newsletter,
    waitlist, contact form), screenshots before and after, and submits.
    Wrapped so a failed autofill degrades gracefully instead of crashing the run.
    Never use this for payments or another person's private data."""
    ts = int(time.time())
    before_path = f"{SCREENSHOT_DIR}/before_{ts}.png"
    after_path = f"{SCREENSHOT_DIR}/after_{ts}.png"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            page.screenshot(path=before_path)

            filled_any = False
            inputs = page.query_selector_all("input, textarea")
            for el in inputs:
                attr_blob = " ".join(
                    filter(
                        None,
                        [
                            el.get_attribute("name"),
                            el.get_attribute("id"),
                            el.get_attribute("placeholder"),
                            el.get_attribute("aria-label"),
                        ],
                    )
                )
                for field_key, aliases in FIELD_ALIASES.items():
                    if field_key in field_values and _matches(attr_blob, aliases):
                        try:
                            el.fill(field_values[field_key])
                            filled_any = True
                        except Exception:
                            pass
                        break

            page.screenshot(path=after_path)

            if not filled_any:
                browser.close()
                return {
                    "success": False,
                    "reason": "no matching form fields found on target page",
                    "before_screenshot": before_path,
                    "after_screenshot": after_path,
                    "final_url": url,
                }

            submit_btn = page.query_selector(
                "button[type=submit], input[type=submit], button:has-text('Submit'), "
                "button:has-text('Subscribe'), button:has-text('Sign up'), button:has-text('Join')"
            )
            if submit_btn:
                submit_btn.click(timeout=5000)
                page.wait_for_timeout(1500)

            final_url = page.url
            page.screenshot(path=after_path)
            browser.close()

            return {
                "success": True,
                "before_screenshot": before_path,
                "after_screenshot": after_path,
                "final_url": final_url,
            }
    except Exception as e:  # noqa: BLE001 - action failures must not crash the run
        return {
            "success": False,
            "reason": str(e),
            "before_screenshot": before_path if os.path.exists(before_path) else None,
            "after_screenshot": None,
            "final_url": url,
        }
