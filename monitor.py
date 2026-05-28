import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import requests
except ImportError:
    print("Missing dependency: pip install requests")
    sys.exit(1)


def _load_env_file() -> None:
    env_file = Path(__file__).parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_load_env_file()


# --- Config ---
STATE_FILE = Path(__file__).parent / "state.json"
LOG_FILE   = Path(__file__).parent / "monitor.log"

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SUPABASE_API_KEY = os.environ.get("SUPABASE_API_KEY", "")

BASE_URL = "https://lsmvcgyecisfeebtcahw.supabase.co/rest/v1"
HEADERS = {
    "apikey":         SUPABASE_API_KEY,
    "authorization":  f"Bearer {SUPABASE_API_KEY}",
    "accept-profile": "public",
    "accept":         "*/*",
}


# --- Night skip ---

def is_night_hours() -> bool:
    hour = datetime.now(ZoneInfo("Asia/Kolkata")).hour
    return hour < 7  # 12am–7am IST


# --- API calls ---

def fetch_deals() -> list[dict]:
    params = {
        "select": "*,campaigns(id,store_name,brand_logo,platform,campaign_type,claim_form_id,max_claims_per_user,daily_rate_limit,claim_limit_period)",
        "is_live": "eq.true",
        "order":   "cashback.desc",
    }
    resp = requests.get(f"{BASE_URL}/deals", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_stats(claim_form_ids: set[str]) -> dict[str, dict]:
    if not claim_form_ids:
        return {}
    ids_param = "(" + ",".join(sorted(claim_form_ids)) + ")"
    params = {
        "select":        "product_id,claim_form_id,today_count,total_count,updated_at",
        "claim_form_id": f"in.{ids_param}",
    }
    resp = requests.get(f"{BASE_URL}/deal_stats", headers=HEADERS, params=params, timeout=15)
    resp.raise_for_status()
    return {str(row["product_id"]): row for row in resp.json()}


# --- Business logic ---

def is_effectively_sold_out(deal: dict, stats_lookup: dict[str, dict]) -> bool:
    if deal["is_sold_out"]:
        return True
    stat = stats_lookup.get(str(deal["product_id"]))
    budget = deal.get("product_budget") or 0
    if stat and budget > 0:
        return stat["total_count"] >= budget
    return False


def build_snapshot(deals: list[dict], stats_lookup: dict[str, dict]) -> dict[str, dict]:
    snap = {}
    for d in deals:
        stat = stats_lookup.get(str(d["product_id"]))
        campaigns = d.get("campaigns") or {}
        snap[str(d["id"])] = {
            "name":                 d["name"],
            "store_name":           campaigns.get("store_name", ""),
            "platform":             campaigns.get("platform", ""),
            "campaign_type":        campaigns.get("campaign_type", ""),
            "cashback":             d["cashback"],
            "net_price":            d["net_price"],
            "original_price":       d["original_price"],
            "claim_link":           d["claim_link"],
            "tryouts_url":          f"https://tryouts.freekaamaal.com/deal/{d['slug']}",
            "image":                d.get("image", ""),
            "product_budget":       d.get("product_budget") or 0,
            "is_sold_out":          d["is_sold_out"],
            "total_count":          stat["total_count"] if stat else None,
            "effectively_sold_out": is_effectively_sold_out(d, stats_lookup),
        }
    return snap


def detect_changes(current: dict, previous: dict) -> list[dict]:
    events = []
    for deal_id, deal in current.items():
        prev = previous.get(deal_id)
        if prev is None:
            events.append({"type": "NEW", "deal": deal, "id": deal_id})
        elif prev["effectively_sold_out"] and not deal["effectively_sold_out"]:
            events.append({"type": "RESTOCK", "deal": deal, "id": deal_id})
        elif deal["product_budget"] > prev["product_budget"]:
            # Seller added more slots
            events.append({"type": "RESTOCK", "deal": deal, "id": deal_id})

    # Available deals first, sold-out ones after
    events.sort(key=lambda e: e["deal"]["effectively_sold_out"])
    return events


# --- Telegram ---

def _format_telegram(event: dict) -> str:
    d     = event["deal"]
    icon  = "NEW" if event["type"] == "NEW" else "RESTOCK"
    label = "New Deal" if event["type"] == "NEW" else "Restocked"
    return (
        f"<b>[{icon}] {label}</b>\n"
        f"{d['name']}\n\n"
        f"Cashback: <b>Rs.{d['cashback']}</b>  |  You pay: <b>Rs.{d['original_price']}</b>  |  Net: <b>Rs.{d['net_price']}</b>\n"
        f"Platform: {d['platform']} / {d['store_name']}\n"
        f"Type: <b>{d['campaign_type'].capitalize()}</b>  |  Budget: {d['product_budget']} slots\n\n"
        f"<a href=\"{d['tryouts_url']}\">View Deal</a>"
    )


def send_telegram(event: dict) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    text  = _format_telegram(event)
    image = event["deal"].get("image", "")
    try:
        if image:
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                json={"chat_id": TELEGRAM_CHAT_ID, "photo": image, "caption": text, "parse_mode": "HTML"},
                timeout=10,
            )
            if resp.ok:
                return
        # fallback to text if no image or photo send failed
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as exc:
        print(f"Telegram send failed: {exc}", flush=True)


# --- Logging ---

def _format_event(event: dict) -> str:
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    d   = event["deal"]
    tag = "NEW    " if event["type"] == "NEW" else "RESTOCK"
    return (
        f"[{ts}] [{event['type']}] {tag} | {d['name']} "
        f"| cashback Rs.{d['cashback']} | net Rs.{d['net_price']} "
        f"| {d['platform']}/{d['store_name']} "
        f"| budget={d['product_budget']} | {d['claim_link']}"
    )


def log_event(event: dict) -> None:
    line = _format_event(event)
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    send_telegram(event)


def log_info(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# --- State persistence ---

def load_state() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(snap: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(snap, f, indent=2, ensure_ascii=False)


# --- Main (runs once per invocation) ---

def main() -> None:
    if is_night_hours():
        log_info("Night hours (12am–7am IST) — skipping.")
        return

    try:
        prev  = load_state()
        deals = fetch_deals()

        claim_form_ids = {d["claim_form_id"] for d in deals if d.get("claim_form_id")}
        stats = fetch_stats(claim_form_ids)

        curr   = build_snapshot(deals, stats)
        events = detect_changes(curr, prev)

        if events:
            for e in events:
                log_event(e)
        else:
            log_info(f"No changes. {len(curr)} live deals checked.")

        save_state(curr)

    except requests.RequestException as exc:
        log_info(f"Network error (state not saved): {exc}")
    except Exception as exc:
        log_info(f"Unexpected error: {exc}")


if __name__ == "__main__":
    main()
