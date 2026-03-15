"""
Fetch AvgMonthVisits from Similarweb Pro API for all companies in the database
and save to the DB. Stops when the cookie expires — update SIMILARWEB_COOKIES
in keys.env and re-run.

Input: All companies from the DB (company.domain = domain used for Similarweb).
Output:
  - Company.extra_data["platform_presence_analytics"]["similarweb"] (full snapshot)
  - Company.monthly_visits (integer for UI/outreach)

Usage:
  Set SIMILARWEB_COOKIES in keys.env (from browser: DevTools → Network →
  copy as cURL → use the -b cookie string), then:
  python run_similarweb_independent.py

Data appears in Intelligence tab → Platform presence analytics. Existing
platform presence (MSN/Yahoo/NewsBreak) is not changed.
"""
import os
import sys
from pathlib import Path

# Project root for imports and keys.env
_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv
load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

cookies = (os.environ.get("SIMILARWEB_COOKIES") or os.environ.get("SIMWEB_COOKIES") or "").strip()
if not cookies:
    cookie_file = os.environ.get("SIMILARWEB_COOKIES_FILE", str(_root / "similarweb_cookies.txt"))
    if os.path.exists(cookie_file):
        with open(cookie_file, "r", encoding="utf-8") as f:
            cookies = "".join(
                line for line in f
                if line.strip() and not line.strip().startswith("#")
            ).strip()
if not cookies:
    print("Add SIMILARWEB_COOKIES to keys.env (or create similarweb_cookies.txt with the cookie string).")
    print("From browser: DevTools → Network → copy as cURL → use the -b value.")
    sys.exit(1)
os.environ["SIMILARWEB_COOKIES"] = cookies

from integrations.analytics.platform_analytics import run_similarweb_for_independent_companies


def main():
    def progress(i, total, domain):
        print(f"  [{i}/{total}] {domain}", end=" ... ", flush=True)

    print("Similarweb sync for independent companies — loading from DB...")
    updated, err = run_similarweb_for_independent_companies(
        progress_callback=progress,
        min_delay_sec=2.0,
        max_delay_sec=5.0,
    )
    if err:
        print("\n" + err)
        sys.exit(1)
    print(f"\nDone. Updated {updated} companies. Open Intelligence → company → Platform presence analytics to view.")


if __name__ == "__main__":
    main()
