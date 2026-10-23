"""Journal v2 tests (run: python3 momentum_v2/tests/test_journal.py)."""
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_engine import pytest  # noqa: E402
from journal.journal import Journal  # noqa: E402
from journal import stats as S  # noqa: E402

UTC = timezone.utc


def tmpj():
    d = tempfile.mkdtemp()
    return Journal(Path(d) / "j.db"), Path(d) / "j.db"


def closed(j, coin, r, exit_dt, scanner="mean_reversion_v2", setup="fast_spike", outcome=None, side="short"):
    tid = j.log_signal(scanner=scanner, direction=side, coin=coin, sl_price=1.1 if side == "short" else 0.9,
                       entry_price=1.0, signal_ts=(exit_dt - timedelta(hours=1)).isoformat(), setup=setup)
    j.capture_exit(tid, 1.0, "TP" if r > 0 else "SL", r_override=r, exit_ts=exit_dt.isoformat())
    return tid


def test_sunday_trades_count_in_their_week():
    j, _ = tmpj()
    now = datetime.now(UTC)
    last_monday = (now - timedelta(days=now.weekday() + 7)).replace(hour=0, minute=0, second=0, microsecond=0)
    closed(j, "SUNUSDT", 1.0, last_monday + timedelta(days=6, hours=15))        # last week's Sunday 15:00
    weeks = j.get_weekly_stats(2)
    assert weeks[f"Week of {last_monday.date().isoformat()}"]["count"] == 1


def test_expectancy_counts_breakevens_as_trades():
    j, _ = tmpj()
    t = datetime.now(UTC) - timedelta(days=1)
    for r in (1.0, 0.05, -1.0):
        closed(j, f"C{r}USDT", r, t)
    sm = j.get_all_time_stats()["summary"]
    assert sm["closed"] == 3 and sm["breakeven"] == 1
    assert sm["expectancy_r"] == pytest.approx(0.05 / 3, abs=1e-3)
    assert sm["win_rate"] == pytest.approx(33.3, abs=0.1) and sm["win_rate_decisive"] == 50.0


def test_recent_trades_really_covers_n_days():
    j, _ = tmpj()
    closed(j, "OLDUSDT", 1.0, datetime.now(UTC) - timedelta(days=10))
    closed(j, "VERYOLDUSDT", 1.0, datetime.now(UTC) - timedelta(days=40))
    coins = [t["coin"] for t in j.get_closed_trades(days_back=14)]
    assert coins == ["OLDUSDT"]


def test_cancelled_orders_are_not_trades():
    j, _ = tmpj()
    tid = j.log_signal(scanner="momentum_v2", direction="long", coin="XUSDT", sl_price=0.9, entry_price=1.0)
    j.update_outcome(tid, "cancelled")
    closed(j, "YUSDT", 1.0, datetime.now(UTC) - timedelta(hours=2), scanner="momentum_v2")
    at = j.get_all_time_stats()
    assert at["count"] == 1 and at["win_rate"] == 100.0
    assert at["breakdowns"]["cancelled"] == 1


def test_v2_bots_have_their_own_breakdown():
    j, _ = tmpj()
    t = datetime.now(UTC) - timedelta(hours=3)
    closed(j, "AUSDT", 1.0, t, scanner="momentum_v2", setup="momentum_breakout", side="long")
    closed(j, "BUSDT", -1.0, t, scanner="mean_reversion_v2", setup="failed_breakout")
    at = j.get_all_time_stats()
    assert set(at["by_scanner"]) == {"momentum_v2", "mean_reversion_v2"}
    assert set(at["breakdowns"]["by_setup"]) == {"momentum_breakout", "failed_breakout"}
    rep = j.format_weekly_report()
    assert "Momentum:" in rep and "Mean reversion:" in rep and "🟢AUSDT".replace("USDT", "") in rep.replace("USDT", "")


def test_old_database_is_upgraded_in_place():
    d = tempfile.mkdtemp()
    db = Path(d) / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT UNIQUE NOT NULL, "
                "scanner TEXT NOT NULL, direction TEXT NOT NULL, coin TEXT NOT NULL, signal_ts TEXT NOT NULL, "
                "entry_price REAL, sl_price REAL, tp_price REAL, quality_score INTEGER, mr_score REAL, flags TEXT, "
                "entry_type TEXT, entry_ts TEXT, exit_ts TEXT, exit_price REAL, pnl_usd REAL, pnl_pct REAL, "
                "r_multiple REAL, outcome TEXT DEFAULT 'pending', level_approach TEXT, volume_regime TEXT, "
                "thesis TEXT, notes TEXT, reviewed INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
    con.commit()
    con.close()
    Journal(db)
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(trades)")}
    assert {"setup", "exit_reason"} <= cols
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM manual_trades").fetchone()[0] == 0


def test_bot_exit_time_and_setup_are_recorded():
    from momentum_v2.reporting import JournalBridge
    from momentum_v2.core import Trade
    j, db = tmpj()
    jb = JournalBridge(str(db), scanner="mean_reversion_v2")
    t = Trade(symbol="FOOUSDT", side="short", level_tf="fast_spike", level=1.0, signal_t=1_792_400_000_000,
              entry_type="market", stop=1.05, target=0.95, target_kind="spike_origin", score=3,
              criteria={"fast_spike": True}, details={}, setup="fast_spike")
    t.entry, t.entry_t = 1.0, 1_792_400_060_000
    jb.on_fill(t)
    t.exit, t.exit_t, t.exit_reason, t.gross_r, t.net_r = 0.95, 1_792_401_000_000, "TP", 1.0, 0.92
    jb.on_exit(t)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    row = dict(con.execute("SELECT setup, exit_reason, exit_ts, pnl_pct, r_multiple FROM trades").fetchone())
    assert row["setup"] == "fast_spike" and row["exit_reason"] == "TP"
    assert S.parse_ts(row["exit_ts"]) == datetime.fromtimestamp(1_792_401_000, tz=UTC)
    assert row["pnl_pct"] == pytest.approx(5.0) and row["r_multiple"] == pytest.approx(0.92)


def test_manual_trade_r_pnl_and_slippage_vs_paper():
    j, _ = tmpj()
    tid = closed(j, "FOOUSDT", 0.9, datetime.now(UTC) - timedelta(hours=1))      # bot paper short, entry 1.0
    mid = j.manual_open("FOOUSDT", "short", entry_price=0.998, stop_price=1.048, size_usd=2000, signal_trade_id=tid)
    res = j.manual_close(mid, exit_price=0.948, fees_usd=2.0)
    # gross: 2000 * 0.05/0.998 = 100.2; minus fees 2 → 98.2; risk_usd = 2000*0.05/0.998 = 100.2
    assert res["pnl_usd"] == pytest.approx(98.2, abs=0.01) and res["r_multiple"] == pytest.approx(0.98, abs=0.01)
    mvp = j.manual_vs_paper()[0]
    assert mvp["paper_r"] == pytest.approx(0.9)
    assert mvp["entry_slippage_pct"] == pytest.approx(0.2, abs=1e-6)              # shorted 0.2% LOWER = worse
    with pytest_raises(ValueError):
        j.manual_open("FOOUSDT", "long", entry_price=1.0, stop_price=1.1)          # stop on the wrong side


class pytest_raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        assert et is not None and issubclass(et, self.exc), "expected an exception"
        return True


def test_reports_run_and_agree():
    j, db = tmpj()
    t = datetime.now(UTC) - timedelta(hours=5)
    for r in (1.0, -1.0, 0.04, 1.2):
        closed(j, f"R{r}USDT", r, t)
    out = subprocess.run([sys.executable, "journal/performance_report.py", "--db", str(db)], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    exp = S.summarize(j._all_rows())["expectancy_r"]
    assert f"exp {exp:+.3f}R" in out.stdout and "By setup" in out.stdout
    spec = importlib.util.spec_from_file_location("standup_report", ROOT / "journal/standup_report.py")
    standup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(standup)
    st = standup._compute_stats(standup._query_stats(db, None))
    assert st["closed"] == 4 and st["expectancy_r"] == exp
    assert "mean_reversion_v2" in st["by_bot"]
    assert "Mean reversion" in standup._format_telegram(st, {"stale": False, "reason": "fresh"}, "test")


def test_reset_script_is_valid_bash():
    assert subprocess.run(["bash", "-n", str(ROOT / "journal/reset_journal.sh")]).returncode == 0


if __name__ == "__main__":
    import traceback
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("PASS", n)
        except Exception:
            fails += 1
            print("FAIL", n)
            traceback.print_exc()
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    sys.exit(1 if fails else 0)
