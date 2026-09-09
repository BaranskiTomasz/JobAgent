import glob
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from flask import Blueprint, request
from flask_sock import Sock

import api_client
from config import QUERY_PRUNING
from collector.query_pruning import suggest_queries_for_review
from db.repositories import session_repository, usage_repository, excluded_search_queries_repository

bp = Blueprint("runner", __name__)
sock = Sock()

ROOT     = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOGS_DIR = os.path.join(ROOT, "data", "logs")

# Prefer the project venv so subprocesses have all dependencies (voyageai etc.)
_venv_python = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PYTHON = _venv_python if os.path.exists(_venv_python) else sys.executable
_KEEP_LOGS = 30

_agent_process: subprocess.Popen | None = None
_lock = threading.Lock()
_run_active = False

# Set by _run_pipeline_ws right before each stage header is written, cleared in
# its finally. Lets /api/agent/status report exact progress for a tab that
# reattaches mid-run (see openRunModal/_attachToActiveRun in dashboard.js)
# without re-deriving it by scanning the log tail, which /api/agent/logs
# truncates to the last 200 lines, too short to still contain early stage
# headers on a run whose current stage has printed thousands of lines since.
_stage_progress: dict | None = None


def init_sock(app):
    sock.init_app(app)


# Sentinel substituted with the real session id once _run_pipeline_ws has one,
# build_stages() runs before the session exists (see its call site below), but
# the COLLECTOR stage needs to reuse the same session id rather than starting
# its own, or JobAgentWeb's concurrent-session guard rejects the second start().
_SESSION_ID_PLACEHOLDER = "__SESSION_ID__"


def _is_run_active() -> bool:
    if _run_active:
        return True
    if _agent_process is not None and _agent_process.poll() is None:
        return True
    from db.repositories import session_repository
    return session_repository.has_active_run()


class _RunGuard:
    """Holds _run_active for the guard's entire lifetime, closing the race
    where two near-simultaneous websocket connections could both pass the
    active-run check. Usage: `with _RunGuard() as acquired: if not acquired:
    ...bail out...`."""

    def __enter__(self) -> bool:
        global _run_active
        with _lock:
            if _is_run_active():
                return False
            _run_active = True
            return True

    def __exit__(self, *_exc):
        global _run_active
        _run_active = False


def _start_session_or_none(ws) -> int | None:
    # session_repository.start() fails server-side (409) if this account
    # already has a running session, the real guard against two concurrent
    # runs, since _RunGuard is only an in-process flag. Sends a friendly
    # message instead of letting the ApiError propagate uncaught.
    try:
        return session_repository.start()
    except api_client.ApiError as e:
        ws.send(f"ERROR: {e.detail}\n")
        return None


@bp.get("/api/agent/status")
def agent_status():
    # started_at lets the frontend show an elapsed-time counter after
    # reopening the run modal mid-run, since a page reload loses any
    # client-side timer. last_status is read unconditionally so a client that
    # missed the live __DONE__ message can still learn the last run's outcome.
    running = _is_run_active()
    latest = session_repository.get_latest()
    started_at = latest.get("started_at") if (running and latest) else None
    last_status = latest.get("status") if latest else None
    return {"running": running, "started_at": started_at, "stage": _stage_progress, "last_status": last_status}


@bp.get("/api/agent/query-suggestions")
def agent_query_suggestions():
    return {"suggestions": suggest_queries_for_review()}


@bp.post("/api/agent/query-suggestions/apply")
def agent_query_suggestions_apply():
    queries = (request.get_json(force=True) or {}).get("queries", [])
    source = QUERY_PRUNING["source"]
    for query in queries:
        excluded_search_queries_repository.exclude(source, query, "excluded by user from Run Agent suggestions")
    return {"excluded": queries}


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Kill process and all its children (handles orphaned Playwright/Chrome on Windows)."""
    try:
        if sys.platform == "win32":
            subprocess.call(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            import signal, os
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        proc.terminate()


@bp.post("/api/agent/stop")
def agent_stop():
    global _agent_process
    from db.repositories import session_repository
    killed = False
    if _agent_process is not None and _agent_process.poll() is None:
        _kill_process_tree(_agent_process)
        _agent_process = None
        killed = True
    session_repository.cancel_active()
    return {"ok": True, "killed": killed}


@bp.get("/api/agent/logs")
def agent_logs():
    path = _latest_log()
    if not path:
        return {"lines": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return {"lines": [l.rstrip("\n") for l in lines[-200:]]}
    except FileNotFoundError:
        return {"lines": []}


@bp.get("/api/agent/log-files")
def agent_log_files():
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "run_*.log")), reverse=True)
    return {"files": [os.path.basename(f) for f in files[:50]]}


def _safe_send(ws, msg: str) -> None:
    try:
        ws.send(msg)
    except Exception:
        pass


def _run_script(ws, script_path: str, args: list[str] | None = None, log_file=None) -> int:
    global _agent_process
    cmd = [PYTHON, "-u", script_path] + (args or [])
    env = os.environ.copy()
    env["PYTHONPATH"] = ROOT
    env["PYTHONIOENCODING"] = "utf-8"
    _agent_process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        bufsize=1,
        cwd=ROOT,
        env=env,
    )
    ws_alive = True
    for line in _agent_process.stdout:
        if log_file:
            log_file.write(line)
            log_file.flush()
        if ws_alive:
            try:
                ws.send(line)
            except Exception:
                ws_alive = False  # keep draining stdout so process doesn't block
    _agent_process.wait()
    return _agent_process.returncode


def _open_log():
    os.makedirs(LOGS_DIR, exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(LOGS_DIR, f"run_{ts}.log")
    _rotate_logs()
    return open(path, "w", encoding="utf-8")


def _rotate_logs():
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "run_*.log")))
    for old in files[:-_KEEP_LOGS]:
        try:
            os.remove(old)
        except OSError:
            pass


def _latest_log() -> str | None:
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "run_*.log")))
    return files[-1] if files else None


# Stages run after a successful collection. EXTRACTOR must precede EVALUATOR,
# dealbreakers.py reads structured_data, and a job never re-enters the unscored
# pool once scored, so extracting later would miss it permanently.
def _post_collect_stages() -> list[tuple[str, str, list[str], bool]]:
    return [
        ("DISTILL PREFERENCES", os.path.join(ROOT, "scripts", "distill_preferences.py"), [], False),
        ("EXTRACTOR",           os.path.join(ROOT, "scripts", "extract_jobs.py"), [], False),
        ("EVALUATOR",           os.path.join(ROOT, "evaluator", "runner.py"), [], False),
        ("PRUNE QUERIES",       os.path.join(ROOT, "scripts", "prune_search_queries.py"), [], False),
        ("AI RANKING",          os.path.join(ROOT, "scripts", "rank_jobs.py"), [], False),
    ]


def _run_pipeline_ws(ws, run_label: str, build_stages) -> None:
    # Shared body for every pipeline websocket handler: acquire _RunGuard,
    # start a session spanning every stage, run each stage in order, and
    # always finish the session, record usage, and send __DONE__, even on
    # error. build_stages is a callable, not a plain list, so a handler
    # needing ws.receive() first (only _agent_run does) reads it inside the
    # guard; return None from it to abort after sending your own error.
    #
    # A stage with stop_if_fails=False that exits non-zero used to be silently
    # swallowed, rendering identically to a clean run in the UI. Every
    # non-zero exit is now logged inline, and the run's final status
    # (done / done_with_errors / error) reaches both the session record and
    # the __DONE__:<status> suffix.
    global _agent_process, _stage_progress

    with _RunGuard() as acquired:
        if not acquired:
            ws.send("ERROR: Agent is already running.\n")
            return

        stages = build_stages()
        if stages is None:
            return

        started_at = usage_repository.now_iso()
        session_id = _start_session_or_none(ws)
        if session_id is None:
            return
        status = "done"
        failed_stages: list[str] = []

        stages = [
            (label, path, [str(session_id) if a == _SESSION_ID_PLACEHOLDER else a for a in args], stop_if_fails)
            for label, path, args, stop_if_fails in stages
        ]

        try:
            with _open_log() as log_file:
                for i, (label, script_path, args, stop_if_fails) in enumerate(stages):
                    _stage_progress = {"index": i + 1, "total": len(stages), "label": label}
                    prefix = "" if i == 0 else "\n"
                    header = f"{prefix}=== {label} ===\n"
                    log_file.write(header)
                    _safe_send(ws, header)
                    exit_code = _run_script(ws, script_path, args, log_file=log_file)
                    if exit_code == 0 and label.startswith("COLLECTOR"):
                        session_repository.mark_collected(session_id)
                    if exit_code != 0:
                        failed_stages.append(label)
                        continuation = "Skipping remaining stages." if stop_if_fails else "Continuing with remaining stages."
                        msg = f"\n{label} failed (exit code {exit_code}). {continuation}\n"
                        log_file.write(msg)
                        _safe_send(ws, msg)
                        if stop_if_fails:
                            break

                if failed_stages:
                    status = "done_with_errors"
                # Deliberately NOT "=== ... ===", that's the exact pattern
                # dashboard.js's stageMatch regex uses to detect a new stage
                # header, so this line would otherwise get miscounted as a 7th
                # stage and push the progress bar/label past 100%.
                summary = f"\nRESULT: {status.upper()}"
                if failed_stages:
                    summary += f" (failed: {', '.join(failed_stages)})"
                summary += "\n"
                log_file.write(summary)
                _safe_send(ws, summary)
        except Exception:
            status = "error"
            raise
        finally:
            session_repository.finish(session_id, jobs_found=0, jobs_scored=0, status=status)
            usage_repository.record_run_summary(run_label, started_at)
            _agent_process = None
            _stage_progress = None
            _safe_send(ws, f"\n__DONE__:{status}\n")


@sock.route("/ws/agent")
def agent_ws(ws):
    _agent_run(ws)


def _agent_run(ws):
    """The actual handler body, factored out of agent_ws so it's callable
    directly in tests, flask_sock's @sock.route decorator discards the
    original function and replaces it with a wrapper that requires a real
    request context, so agent_ws itself can't be invoked outside a live
    WebSocket connection."""
    def build_stages():
        raw_params = ws.receive()
        try:
            params = json.loads(raw_params)
        except Exception:
            params = {}

        if params.get("since_last_run"):
            days = session_repository.days_since_last_collection()
        else:
            try:
                days = int(params.get("days", 1))
            except (ValueError, TypeError):
                ws.send("ERROR: Invalid parameter 'days', must be an integer.\n")
                return None

        collector_args = ["--days", str(days), "--session-id", _SESSION_ID_PLACEHOLDER]
        stages = [(f"COLLECTOR (days={days})", os.path.join(ROOT, "collector", "runner.py"), collector_args, True)]
        stages += _post_collect_stages()
        return stages

    _run_pipeline_ws(ws, "run_agent", build_stages)


@sock.route("/ws/backfill")
def backfill_ws(ws):
    _backfill_run(ws)


def _backfill_run(ws):
    """See _agent_run's docstring for why this is factored out of backfill_ws."""
    def build_stages():
        return [
            ("BACKFILL DESCRIPTIONS", os.path.join(ROOT, "scripts", "backfill_descriptions.py"), [], True),
            ("EVALUATOR", os.path.join(ROOT, "evaluator", "runner.py"), [], False),
        ]

    _run_pipeline_ws(ws, "backfill", build_stages)


@sock.route("/ws/reevaluate-rejected")
def reevaluate_rejected_ws(ws):
    _reevaluate_rejected_run(ws)


def _reevaluate_rejected_run(ws):
    """See _agent_run's docstring for why this is factored out of reevaluate_rejected_ws."""
    def build_stages():
        return [("RE-EVALUATE AUTO-REJECTED", os.path.join(ROOT, "scripts", "reevaluate_rejected.py"), [], False)]

    _run_pipeline_ws(ws, "reevaluate_rejected", build_stages)


@sock.route("/ws/rescore-new")
def rescore_new_ws(ws):
    _rescore_new_run(ws)


def _rescore_new_run(ws):
    """See _agent_run's docstring for why this is factored out of rescore_new_ws."""
    def build_stages():
        return [
            ("DISTILL PREFERENCES", os.path.join(ROOT, "scripts", "distill_preferences.py"), [], False),
            ("RE-SCORE NEW JOBS", os.path.join(ROOT, "scripts", "rescore_new.py"), [], False),
        ]

    _run_pipeline_ws(ws, "rescore_new", build_stages)


@sock.route("/ws/rank")
def rank_ws(ws):
    _rank_run(ws)


def _rank_run(ws):
    """See _agent_run's docstring for why this is factored out of rank_ws."""
    def build_stages():
        return [("AI RANKING (Voyage + Claude Opus)", os.path.join(ROOT, "scripts", "rank_jobs.py"), [], False)]

    _run_pipeline_ws(ws, "rank", build_stages)
