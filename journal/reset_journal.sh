#!/usr/bin/env bash
# Delete the old (contaminated) journal and start a fresh one for the v2 bots.
#
# Run on the VPS, from the workspace root, AFTER PRs #3-#7 are merged and deployed:
#     cd /home/openclaw/workspace && bash journal/reset_journal.sh
#
# Safety checks first — it refuses to run if:
#   * a cron job still runs the old scanners with --journal (they would refill
#     the new journal with junk straight away), or
#   * a v2 bot has an open paper trade (its exit could not be recorded).
# It asks you to type DELETE before removing anything. Deletion is permanent.
set -euo pipefail

WS="$(pwd)"
DB="$WS/journal/journal.db"
[ -f "$WS/journal/journal.py" ] || { echo "Run this from the workspace root (where journal/ lives)."; exit 1; }

echo "== 1. Old cron jobs"
if crontab -l 2>/dev/null | grep -v '^\s*#' | grep -Eq 'realtime_scanner|cron_spicy_report|scan_and_report|_morning_send|exit_monitor|velo_spicy_scanner.*--journal|momentum_scanner.*--journal'; then
  echo "   Still in your crontab — remove these lines first (crontab -e):"
  crontab -l | grep -v '^\s*#' | grep -E 'realtime_scanner|cron_spicy_report|scan_and_report|_morning_send|exit_monitor|velo_spicy_scanner|momentum_scanner'
  exit 1
fi
echo "   ok — no old scanner jobs"

echo "== 2. Stop the v2 bots while the journal is swapped"
for svc in momentum-v2 mean-reversion-v2; do
  systemctl --user stop "$svc" 2>/dev/null && echo "   stopped $svc" || echo "   $svc not running"
done

echo "== 3. Open v2 paper trades"
OPEN=$(python3 - <<'PY'
import json, pathlib
n = 0
for f in ("momentum_v2/data/live_state.json", "momentum_v2/data/mr_live_state.json"):
    p = pathlib.Path(f)
    if p.exists():
        st = json.loads(p.read_text())
        n += sum(1 for e in st.get("engines", {}).values() if e.get("trade") and e["trade"].get("status") == "open")
print(n)
PY
)
if [ "$OPEN" != "0" ]; then
  echo "   $OPEN open v2 paper trade(s) — restart the bots and wait for them to close first:"
  echo "   systemctl --user start momentum-v2 mean-reversion-v2"
  exit 1
fi
echo "   ok — none open"

echo "== 4. Delete the old journal"
if [ -f "$DB" ]; then
  python3 - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
for row in con.execute("SELECT scanner, outcome, COUNT(*) FROM trades GROUP BY scanner, outcome"):
    print("   ", row)
PY
  read -r -p "   Type DELETE to permanently remove $DB: " ans
  [ "$ans" = "DELETE" ] || { echo "   Cancelled — nothing deleted."; exit 1; }
  rm -f "$DB" "$DB-wal" "$DB-shm"
  echo "   deleted"
else
  echo "   no old journal found"
fi

echo "== 5. Create the fresh journal"
python3 -c "import sys; sys.path.insert(0, '.'); from journal import Journal; Journal(); print('   created', '$DB')"

echo "== 6. Restart the v2 bots"
for svc in momentum-v2 mean-reversion-v2; do
  systemctl --user start "$svc" 2>/dev/null && echo "   started $svc" || echo "   $svc not installed (see momentum_v2/README.md)"
done
echo "Done. Check: python3 journal/performance_report.py"
