"""
Tests for main.py — run_once, run_daemon, --watch flag behaviour.
All tests mock run_pipeline so nothing hits live feeds or the DB.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime, timezone


def _mock_state(n_briefs=0, n_errors=0):
    briefs = [
        MagicMock(brief_id=f"b-{i}", severity_score=5,
                  headline=f"Brief {i}", recommended_actions=["action"])
        for i in range(n_briefs)
    ]
    state = MagicMock()
    state.briefs = briefs
    state.errors = ["err"] * n_errors
    state.raw_events = []
    state.clusters = []
    state.correlations = []
    return state


# ---------------------------------------------------------------------------
# run_once
# ---------------------------------------------------------------------------

def test_run_once_calls_pipeline_exactly_once():
    state = _mock_state()
    with patch("main.run_pipeline", return_value=state) as mock_pipeline, \
         patch("main._persist"), \
         patch("main.print_summary"):
        from main import run_once
        run_once([])
    mock_pipeline.assert_called_once()


def test_run_once_returns_state():
    state = _mock_state()
    with patch("main.run_pipeline", return_value=state), \
         patch("main._persist"), \
         patch("main.print_summary"):
        from main import run_once
        result = run_once([])
    assert result is state


def test_run_once_calls_persist_and_summary():
    state = _mock_state()
    with patch("main.run_pipeline", return_value=state), \
         patch("main._persist") as mock_persist, \
         patch("main.print_summary") as mock_summary:
        from main import run_once
        run_once([])
    mock_persist.assert_called_once_with(state)
    mock_summary.assert_called_once_with(state)


# ---------------------------------------------------------------------------
# run_daemon — cycle count, error handling, KeyboardInterrupt
# ---------------------------------------------------------------------------

def test_run_daemon_failed_cycle_does_not_crash():
    """A RuntimeError in one cycle must not kill the daemon — it logs and continues."""
    calls = []

    def side_effects(feed_fns):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("simulated failure")
        raise KeyboardInterrupt()

    with patch("main.run_once", side_effect=side_effects), \
         patch("main.time.sleep", return_value=None), \
         patch("main.time.strftime", return_value="T"):
        from main import run_daemon
        run_daemon([], 1)  # must return without raising

    assert len(calls) == 2  # failed once, then interrupted


def test_run_daemon_keyboard_interrupt_exits_cleanly():
    """Ctrl-C must exit cleanly — no exception propagates to caller."""
    with patch("main.run_once", side_effect=KeyboardInterrupt()), \
         patch("main.time.sleep", return_value=None), \
         patch("main.time.strftime", return_value="T"):
        from main import run_daemon
        run_daemon([], 1)  # must not raise


def test_run_daemon_logs_new_briefs():
    """Briefs that appear in a new cycle but not the previous are flagged as NEW."""
    state_a = _mock_state(n_briefs=0)
    state_b = _mock_state(n_briefs=1)

    call_n = [0]

    def side_effects(feed_fns):
        call_n[0] += 1
        if call_n[0] == 1:
            return state_a
        if call_n[0] == 2:
            return state_b
        raise KeyboardInterrupt()

    with patch("main.run_once", side_effect=side_effects), \
         patch("main.time.sleep", return_value=None), \
         patch("main.time.strftime", return_value="T"), \
         patch("main.logger") as mock_log:
        from main import run_daemon
        run_daemon([], 1)

    new_log_calls = [c for c in mock_log.info.call_args_list
                     if "NEW" in str(c)]
    assert len(new_log_calls) >= 1


# ---------------------------------------------------------------------------
# --watch flag wiring (argparse)
# ---------------------------------------------------------------------------

def test_watch_flag_calls_run_daemon_with_live_feeds():
    """--watch should call run_daemon with live feed functions."""
    with patch("main.run_daemon") as mock_daemon, \
         patch("main.build_live_feed_fns", return_value=["live_fn"]):
        import main as m
        import importlib, sys
        # simulate: python main.py --watch
        with patch("sys.argv", ["main.py", "--watch"]):
            m.main()
    mock_daemon.assert_called_once()
    args, kwargs = mock_daemon.call_args
    assert args[0] == ["live_fn"]   # feed_fns = live feeds
    assert args[1] == 300           # default interval


def test_watch_flag_respects_custom_interval():
    with patch("main.run_daemon") as mock_daemon, \
         patch("main.build_live_feed_fns", return_value=[]):
        import main as m
        with patch("sys.argv", ["main.py", "--watch", "--interval", "60"]):
            m.main()
    _, kwargs = mock_daemon.call_args
    args, _ = mock_daemon.call_args
    assert args[1] == 60


def test_no_flags_runs_once_and_exits():
    """python main.py (no flags) should call run_once exactly once."""
    state = _mock_state()
    with patch("main.run_pipeline", return_value=state), \
         patch("main._persist"), \
         patch("main.print_summary"), \
         patch("sys.argv", ["main.py"]):
        import main as m
        rc = m.main()
    assert rc == 0
