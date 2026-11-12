"""Enterprise supervisor for 24/7 continuous AlphaQuant AI Pro deployment."""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# Safe fallback for headless pythonw execution where stdout/stderr are None
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import psutil

from dashboard.backend.logging_config import setup_logging
from dashboard.run_dashboard import check_port_status

setup_logging()
logger = logging.getLogger("server")

RUNTIME_DIR = PROJECT_ROOT / "runtime"
SUPERVISOR_PID_FILE = RUNTIME_DIR / "supervisor.pid"
SERVER_PID_FILE = RUNTIME_DIR / "alphaquant.pid"

VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON_EXE = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable


def is_valid_process(pid: int, expected_keyword: str) -> bool:
    """Verifies that `pid` is currently running and matches `expected_keyword` in cmdline."""
    try:
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        cmdline = " ".join(p.cmdline()).lower()
        return expected_keyword.lower() in cmdline
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def get_active_supervisor_pid() -> int | None:
    """Returns active supervisor PID if one is running, cleaning up stale PID file if not."""
    if not SUPERVISOR_PID_FILE.exists():
        return None
    try:
        content = SUPERVISOR_PID_FILE.read_text(encoding="utf-8").strip()
        spid = int(content)
        if is_valid_process(spid, "alphaquant_supervisor"):
            return spid
        else:
            logger.info("Found stale supervisor PID file (%d). Cleaning up.", spid)
            SUPERVISOR_PID_FILE.unlink(missing_ok=True)
            return None
    except Exception:
        try:
            SUPERVISOR_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return None


def get_active_server_pid() -> int | None:
    """Returns active server PID if one is running, cleaning up stale PID file if not."""
    if not SERVER_PID_FILE.exists():
        return None
    try:
        content = SERVER_PID_FILE.read_text(encoding="utf-8").strip()
        srv_pid = int(content)
        if is_valid_process(srv_pid, "python"):
            return srv_pid
        else:
            SERVER_PID_FILE.unlink(missing_ok=True)
            return None
    except Exception:
        try:
            SERVER_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return None


class AlphaQuantSupervisor:
    """Watchdog and lifecycle manager for AlphaQuant AI Pro."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8000):
        self.host = host
        self.port = port
        self.child_proc: subprocess.Popen | None = None
        self.running = True
        self.consecutive_crashes = 0
        self.recent_crash_timestamps: list[float] = []
        self.initial_backoff = 2.0
        self.max_backoff = 30.0

    def write_supervisor_pid(self):
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        SUPERVISOR_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    def cleanup_pids(self):
        try:
            if SUPERVISOR_PID_FILE.exists():
                content = SUPERVISOR_PID_FILE.read_text(encoding="utf-8").strip()
                if content == str(os.getpid()):
                    SUPERVISOR_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def setup_signal_handlers(self):
        def _handler(sig, frame):
            logger.info("Supervisor received stop signal (%s). Initiating graceful shutdown...", sig)
            self.shutdown()

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
        except Exception:
            pass

    def shutdown(self):
        """Gracefully terminates child process and cleans up."""
        self.running = False
        if self.child_proc and self.child_proc.poll() is None:
            logger.info("Terminating AlphaQuant child server (PID: %d)...", self.child_proc.pid)
            try:
                self.child_proc.terminate()
                self.child_proc.wait(timeout=6.0)
            except subprocess.TimeoutExpired:
                logger.warning("Child process did not exit within 6s; force killing...")
                self.child_proc.kill()
                self.child_proc.wait()
            except Exception as e:
                logger.error("Error shutting down child: %s", e)

        self.cleanup_pids()
        try:
            if SERVER_PID_FILE.exists():
                SERVER_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        logger.info("AlphaQuant supervisor stopped cleanly.")

    def run(self):
        # 1. Single-Instance Check for Supervisor
        active_spid = get_active_supervisor_pid()
        if active_spid is not None and active_spid != os.getpid():
            logger.info("Supervisor is already active (PID %d). No duplicate supervisor started.", active_spid)
            print(f"[SUPERVISOR] Supervisor is already running (PID: {active_spid}). Exiting duplicate invocation.")
            return

        self.setup_signal_handlers()
        self.write_supervisor_pid()
        atexit.register(self.cleanup_pids)

        logger.info("AlphaQuant Supervisor started (PID: %d, Host: %s, Port: %d)",
                    os.getpid(), self.host, self.port)

        # 2. Reconcile Existing Server Processes on Port 8000
        status, health = check_port_status(self.host, self.port)
        if status == "foreign":
            logger.critical("Port %d is occupied by another application. Supervisor cannot start AlphaQuant.", self.port)
            print(f"[SUPERVISOR] Error: Port {self.port} occupied by another application.")
            self.cleanup_pids()
            sys.exit(1)

        elif status == "alphaquant":
            # An AlphaQuant server is already running, but NO other supervisor is running.
            # This is an orphan server. Reconcile by gracefully terminating it and adopting.
            orphan_pid = health.get("pid") if health else None
            if not orphan_pid:
                orphan_pid = get_active_server_pid()
            logger.warning("Detected orphan AlphaQuant server on port %d (PID %s). Performing controlled restart to establish full supervision.",
                           self.port, orphan_pid)
            print(f"[SUPERVISOR] Reconciling orphan AlphaQuant server (PID {orphan_pid})...")
            if orphan_pid and psutil.pid_exists(int(orphan_pid)):
                try:
                    p = psutil.Process(int(orphan_pid))
                    p.terminate()
                    p.wait(timeout=6.0)
                except Exception as ex:
                    logger.debug("Orphan termination notice: %s", ex)

            # Wait for port to become free
            for _ in range(12):
                p_stat, _ = check_port_status(self.host, self.port)
                if p_stat == "free":
                    break
                time.sleep(0.5)

        runner_script = PROJECT_ROOT / "dashboard" / "run_dashboard.py"

        # 3. Main Supervision Loop
        while self.running:
            # Circuit breaker check: > 5 crashes within 30 seconds
            now = time.time()
            self.recent_crash_timestamps = [t for t in self.recent_crash_timestamps if now - t < 30.0]
            if len(self.recent_crash_timestamps) >= 5:
                logger.critical("Circuit breaker tripped: 5 crashes in 30 seconds. Pausing for 60s before retry...")
                print("[SUPERVISOR] Circuit breaker tripped (excessive crashes). Pausing 60s...")
                for _ in range(60):
                    if not self.running:
                        break
                    time.sleep(1)
                self.recent_crash_timestamps.clear()
                self.consecutive_crashes = 0
                if not self.running:
                    break

            # Launch child server process
            cmd = [
                PYTHON_EXE,
                str(runner_script),
                "--host", self.host,
                "--port", str(self.port),
                "--no-browser",
            ]
            logger.info("Supervisor spawning child: %s", " ".join(cmd))
            start_time = time.time()

            try:
                env = os.environ.copy()
                env["ALPHAQUANT_SUPERVISED"] = "1"
                env["ALPHAQUANT_NO_BROWSER"] = "1"

                self.child_proc = subprocess.Popen(
                    cmd,
                    cwd=str(PROJECT_ROOT),
                    env=env,
                )

                # Monitor child process & periodically verify /api/health
                health_probe_counter = 0
                while self.running:
                    ret_code = self.child_proc.poll()
                    if ret_code is not None:
                        # Process terminated
                        run_duration = time.time() - start_time
                        logger.warning("AlphaQuant server exited with returncode %s after %.1fs", ret_code, run_duration)

                        if not self.running:
                            break  # Supervisor stopping intentionally

                        # Record crash
                        self.recent_crash_timestamps.append(time.time())
                        if run_duration > 60.0:
                            self.consecutive_crashes = 1
                        else:
                            self.consecutive_crashes += 1

                        delay = min(self.max_backoff, self.initial_backoff * (2 ** (self.consecutive_crashes - 1)))
                        logger.info("Auto-recovery: restarting AlphaQuant in %.1f seconds (crash count: %d)...",
                                    delay, self.consecutive_crashes)
                        print(f"[SUPERVISOR] Server exited (code {ret_code}). Restarting in {delay:.1f}s...")
                        
                        sleep_end = time.time() + delay
                        while time.time() < sleep_end and self.running:
                            time.sleep(0.5)

                        break  # Break inner loop to restart child

                    # Periodic health probe every 10 seconds
                    health_probe_counter += 1
                    if health_probe_counter >= 5:
                        health_probe_counter = 0
                        p_stat, _ = check_port_status(self.host, self.port)
                        if p_stat == "free":
                            logger.warning("Server process PID %d is alive but port %d is not responding. Checking health...",
                                           self.child_proc.pid, self.port)

                    time.sleep(2.0)

            except Exception as e:
                logger.error("Supervisor encountered unexpected error: %s", e)
                time.sleep(2.0)

        self.shutdown()


def stop_running_alphaquant(host: str = "127.0.0.1", port: int = 8000) -> bool:
    """Finds and terminates any running AlphaQuant supervisor and server gracefully."""
    killed_any = False

    # 1. Stop supervisor if running
    spid = get_active_supervisor_pid()
    if spid is not None:
        try:
            proc = psutil.Process(spid)
            logger.info("Stopping supervisor PID %d", spid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
            killed_any = True
        except Exception as e:
            logger.warning("Could not stop supervisor PID: %s", e)
        finally:
            SUPERVISOR_PID_FILE.unlink(missing_ok=True)

    # 2. Stop server process
    server_pid = get_active_server_pid()
    status, health = check_port_status(host, port)
    if status == "alphaquant" and health:
        h_pid = health.get("pid") or (health.get("process") or {}).get("pid")
        if h_pid:
            server_pid = int(h_pid)

    if server_pid and psutil.pid_exists(server_pid):
        try:
            p = psutil.Process(server_pid)
            if "python" in p.name().lower():
                logger.info("Stopping AlphaQuant server PID %d", server_pid)
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
                killed_any = True
        except Exception as e:
            logger.warning("Could not stop server PID %d: %s", server_pid, e)
        finally:
            SERVER_PID_FILE.unlink(missing_ok=True)

    # Wait for port to become free
    for _ in range(12):
        s_status, _ = check_port_status(host, port)
        if s_status == "free":
            break
        time.sleep(0.5)

    return killed_any


def query_status(host: str = "127.0.0.1", port: int = 8000):
    """Prints detailed, structured supervisor and server status."""
    spid = get_active_supervisor_pid()
    status, health = check_port_status(host, port)

    print("=" * 65)
    print("   STATUS: AlphaQuant AI Pro - System Status")
    print("=" * 65)
    print(f" * Supervisor Status: {'RUNNING (PID ' + str(spid) + ')' if spid else 'STOPPED'}")

    if status == "free":
        print(f" * Server Port {port}:  FREE")
        print(f" * Server Mode:       OFFLINE")
    elif status == "foreign":
        print(f" * Server Port {port}:  OCCUPIED (Foreign application)")
        print(f" * Server Mode:       CONFLICT")
    elif status == "alphaquant" and health:
        proc_info = health.get("process", {})
        mkt_info = health.get("market_data", {})
        pt_info = health.get("paper_trading", {})
        srv_pid = health.get("pid", proc_info.get("pid"))

        mode_str = f"SUPERVISED (PID {srv_pid})" if spid else f"ORPHAN (PID {srv_pid} - running without active supervisor)"
        print(f" * Server Port {port}:  ALPHAQUANT")
        print(f" * Server Mode:       {mode_str}")
        print(f" * Server PID:        {srv_pid}")
        print(f" * Uptime:            {health.get('uptime_seconds')} seconds")
        print(f" * Memory Usage:      {proc_info.get('memory_mb')} MB (CPU: {proc_info.get('cpu_percent')}%)")

        mkt_stat = mkt_info.get("status", "UNKNOWN")
        is_conn = mkt_info.get("is_connected", False)
        pairs_cnt = mkt_info.get("pairs_loaded", 0)
        stale_s = mkt_info.get("stale_seconds")
        err_msg = mkt_info.get("last_error")

        if mkt_stat == "LIVE":
            feed_desc = f"LIVE ({pairs_cnt} pairs)"
        elif mkt_stat == "DELAYED":
            feed_desc = f"DELAYED ({stale_s}s stale, {pairs_cnt} pairs)"
        else:
            feed_desc = f"DISCONNECTED ({pairs_cnt} pairs, reason: {err_msg or 'no recent response'})"

        print(f" * Market Feed:       {feed_desc}")
        print(f" * Paper Portfolio:   ${pt_info.get('balance')} USDT ({pt_info.get('open_positions')} open, {pt_info.get('total_trades')} closed)")
    print("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="AlphaQuant AI Pro Supervisor Watchdog")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=8000, help="Server port")
    parser.add_argument("--stop", action="store_true", help="Stop running supervisor and server")
    parser.add_argument("--status", action="store_true", help="Display status and exit")
    parser.add_argument("--check-active", action="store_true", help="Print active supervisor PID if running")
    args = parser.parse_args()

    if args.check_active:
        sp = get_active_supervisor_pid()
        if sp:
            print(f"ACTIVE_SUPERVISOR_PID:{sp}")
        return

    if args.stop:
        stopped = stop_running_alphaquant(args.host, args.port)
        if stopped:
            print("[INFO] AlphaQuant AI Pro has been stopped successfully.")
        else:
            print("[INFO] AlphaQuant AI Pro was not running.")
        return

    if args.status:
        query_status(args.host, args.port)
        return

    supervisor = AlphaQuantSupervisor(host=args.host, port=args.port)
    supervisor.run()


if __name__ == "__main__":
    main()
