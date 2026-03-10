"""
Fetch Similarweb AvgMonthVisits for all independent companies and save to
Company.extra_data["platform_presence_analytics"]["similarweb"]. Data appears
in Intelligence tab → Platform presence analytics expander. Existing platform
presence (MSN/Yahoo/NewsBreak) is not changed.

Usage:
  Set SIMILARWEB_COOKIES in keys.env, then:
  python run_similarweb_independent.py
"""
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from dotenv import load_dotenv

load_dotenv(_root / "keys.env", override=True)
load_dotenv(_root / ".env")

cookies = __import__("os").environ.get("SIMILARWEB_COOKIES", "").strip()
if not cookies:
    print("Add SIMILARWEB_COOKIES to keys.env (from browser: DevTools > Network > copy as cURL > use -b value)")
    sys.exit(1)

from platform_analytics import run_similarweb_for_independent_companies


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
