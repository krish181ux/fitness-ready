#!/bin/zsh
# Garmin sync, run by launchd at 09:00, 11:00 and 21:00
# (~/Library/LaunchAgents/com.fitness-ready.daily-sync.plist).
# Pulls the last 30 days (already-final days are reused) into wearable-real.duckdb.
# launchd starts with a bare environment, so the interpreter path is absolute.
# It notifies (macOS) when the sync fails or last night's sleep is missing or
# untrustworthy, or a model key behind LiteLLM is rejected; a quiet run means all is well.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=/Users/vamsikpalle/.pyenv/versions/3.12.8/bin/python
LOG=~/Library/Logs/fitness-ready-sync.log
# The 09:00 run is an early sync so morning questions see last night; the phone
# may not have uploaded yet, so it only loads. The sleep and key checks run at
# 11:00 and 21:00 (launchd gives every run the same arguments, hence the hour).
EARLY_RUN=$([[ $(date +%H) == 09 ]] && echo 1 || echo 0)

notify() {
  # $1 title, $2 message. Quotes are stripped so they can't break the AppleScript.
  /usr/bin/osascript -e "display notification \"${2//\"/}\" with title \"${1//\"/}\"" || true
}

on_exit() {
  local code=$?
  if (( code != 0 )); then
    notify "Garmin sync failed" "Exit $code. Details in $LOG. An expired Garmin login needs: python -m fetcher.pull in a terminal."
    echo "=== $(date '+%F %T') sync FAILED (exit $code)"
  fi
}
trap on_exit EXIT

echo "=== $(date '+%F %T') sync start"
$PYTHON -m fetcher.pull
$PYTHON -m loader.load_garmin raw/api --db wearable-real.duckdb
$PYTHON -c "import duckdb; c=duckdb.connect('wearable-real.duckdb', read_only=True); print('latest sleep:', c.execute('select date, total_min, sleep_score from sleep order by date desc limit 1').fetchone())"
if (( EARLY_RUN )); then
  echo "=== $(date '+%F %T') sync done (early run: checks skipped)"
  exit 0
fi
if ! problem=$($PYTHON -m fetcher.check --db wearable-real.duckdb); then
  echo "check: $problem"
  notify "Last night's sleep" "$problem"
fi
# A dead model key is otherwise invisible: chat falls back to Gemini's free tier.
if ! problem=$($PYTHON -m fetcher.llm_check); then
  echo "llm: $problem"
  notify "Coach models" "${problem//$'\n'/ | }"
fi
echo "=== $(date '+%F %T') sync done"
