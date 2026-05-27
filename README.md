# Deals Monitor

Polls the FreeKaMaal deals API every 6 minutes and sends a Telegram alert when:

- A **new deal** appears
- A **sold-out deal is restocked** (budget increased or claims reset)

Runs silently between **12am – 7am IST** to avoid unnecessary noise.

## How it works

1. Fetches all live deals + their claim stats
2. Compares against the last snapshot (`state.json`)
3. Detects new deals and restocks
4. Sends a Telegram message and updates the snapshot

## Setup

1. Fork / clone this repo
2. Add two GitHub Secrets (`Settings → Secrets → Actions`):
   - `TELEGRAM_TOKEN` — your Telegram bot token
   - `TELEGRAM_CHAT_ID` — your Telegram chat ID
3. Enable GitHub Actions — the workflow runs automatically on the cron schedule

## State persistence

`state.json` is committed back to the repo after each run. It stores the last known status of every deal and is used to diff against the next poll.
