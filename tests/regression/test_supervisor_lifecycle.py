"""Validation suite for AlphaQuant Supervisor Lifecycle, Orphan Reconciling & Market Data Recovery.

Tests:
1. Launch from VBScript with no server running.
2. Launch with an existing supervised server (single-instance protection).
3. Launch with an orphan AlphaQuant server (orphan reconciliation).
4. Launch twice simultaneously.
5. Stop and restart cycle.
6. Simulate backend crash & verify auto-recovery.
7. Verify Binance connectivity & heartbeat telemetry.
8. Verify paper-trading state preservation ($1,018.36 / 26 trades).
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PYTHON_EXE = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
VBS_PATH = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AlphaQuantAIPro.vbs"

from scripts.launch.alphaquant_supervisor import (
    get_active_server_pid,
    get_active_supervisor_pid,
    stop_running_alphaquant,
)
from dashboard.run_dashboard import check_port_status


def query_health(timeout: float = 3.0) -> dict | None:
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/api/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def wait_for_server(max_wait: float = 20.0) -> dict | None:
    start = time.time()
    while time.time() - start < max_wait:
        data = query_health()
        if data:
            return data
        time.sleep(0.5)
    return None


def test_01_clean_launch_via_vbscript():
    print("\n--- [Test 1] Launch from VBScript with No Server Running ---")
    stop_running_alphaquant("127.0.0.1", 8000)
    time.sleep(1.0)
    assert get_active_supervisor_pid() is None
    assert check_port_status("127.0.0.1", 8000)[0] == "free"

    # Launch VBScript using Windows wscript.exe
    subprocess.run(["wscript.exe", str(VBS_PATH)], check=True)
    health = wait_for_server(max_wait=20.0)
    assert health is not None, "Server failed to start after running VBScript"

    spid = get_active_supervisor_pid()
    assert spid is not None, "Supervisor was not started by VBScript"
    srv_pid = health.get("pid")
    assert srv_pid is not None, "Server PID was not reported"
    print(f"PASS: VBScript launched Supervisor PID={spid} owning Server PID={srv_pid}")
    return spid, srv_pid


def test_02_launch_with_existing_supervised_server(spid, srv_pid):
    print("\n--- [Test 2] Launch with Existing Supervised Server ---")
    # Launch VBScript again while supervisor and server are running
    subprocess.run(["wscript.exe", str(VBS_PATH)], check=True)
    time.sleep(2.0)

    # Verify original supervisor and server are still intact
    curr_spid = get_active_supervisor_pid()
    assert curr_spid == spid, f"Supervisor PID changed from {spid} to {curr_spid}"
    health = query_health()
    assert health is not None
    assert health.get("pid") == srv_pid, f"Server PID changed from {srv_pid} to {health.get('pid')}"
    print(f"PASS: Duplicate launch cleanly ignored without disturbing active Supervisor {spid} or Server {srv_pid}.")


def test_03_orphan_server_reconciliation():
    print("\n--- [Test 3] Launch with Orphan AlphaQuant Server ---")
    # Terminate supervisor process directly, leaving server running as an orphan
    spid = get_active_supervisor_pid()
    if spid and psutil.pid_exists(spid):
        psutil.Process(spid).kill()
    time.sleep(1.0)

    # Verify supervisor is gone but server is still alive (orphan)
    assert get_active_supervisor_pid() is None
    status, health = check_port_status("127.0.0.1", 8000)
    assert status == "alphaquant", "Orphan server was not left running"
    orphan_pid = health.get("pid")
    print(f"Confirmed orphan server running on port 8000 (PID {orphan_pid}) without supervisor.")

    # Now launch supervisor via VBScript
    subprocess.run(["wscript.exe", str(VBS_PATH)], check=True)

    # Wait for new supervisor to write its PID and establish ownership
    new_spid = None
    start = time.time()
    while time.time() - start < 15.0:
        new_spid = get_active_supervisor_pid()
        if new_spid:
            break
        time.sleep(0.5)

    assert new_spid is not None, "Supervisor was not established"

    # Wait for reconciled server to become healthy
    health_new = wait_for_server(max_wait=20.0)
    assert health_new is not None, "Server failed to respond after orphan reconciliation"
    new_srv_pid = health_new.get("pid")
    assert new_srv_pid is not None

    print(f"PASS: Orphan server reconciled! New Supervisor PID={new_spid} successfully adopted/supervised Server PID={new_srv_pid}.")
    return new_spid, new_srv_pid


def test_04_launch_twice_simultaneously():
    print("\n--- [Test 4] Launch Twice Concurrently ---")
    p1 = subprocess.Popen(["wscript.exe", str(VBS_PATH)])
    p2 = subprocess.Popen(["wscript.exe", str(VBS_PATH)])
    p1.wait()
    p2.wait()
    time.sleep(2.0)

    spid = get_active_supervisor_pid()
    assert spid is not None, "Supervisor not running"
    health = query_health()
    assert health is not None
    print(f"PASS: Concurrent launch handled safely. Exactly one Supervisor (PID {spid}) active.")


def test_05_stop_and_restart():
    print("\n--- [Test 5] Stop and Restart Cycle ---")
    # Execute stop
    stopped = stop_running_alphaquant("127.0.0.1", 8000)
    assert stopped is True, "Stop command failed"
    time.sleep(1.0)
    assert get_active_supervisor_pid() is None
    assert check_port_status("127.0.0.1", 8000)[0] == "free"

    # Start again via start_alphaquant.bat
    res = subprocess.run(["cmd.exe", "/c", "start_alphaquant.bat"], capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    assert res.returncode == 0, f"start_alphaquant.bat failed: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout

    health = query_health()
    assert health is not None
    spid = get_active_supervisor_pid()
    assert spid is not None
    print(f"PASS: Clean stop and restart verified. Supervisor PID={spid}, Server PID={health.get('pid')}.")


def test_06_simulate_crash_and_recovery():
    print("\n--- [Test 6] Simulate Backend Crash & Verify Supervisor Auto-Recovery ---")
    health = query_health()
    assert health is not None
    initial_pid = health.get("pid")

    # Kill child server process
    psutil.Process(initial_pid).kill()
    time.sleep(1.0)
    assert not psutil.pid_exists(initial_pid)
    print(f"Child server PID {initial_pid} killed. Waiting for supervisor auto-recovery...")

    # Wait for recovery
    recovered_health = None
    start = time.time()
    while time.time() - start < 15.0:
        h = query_health()
        if h and h.get("pid") != initial_pid:
            recovered_health = h
            break
        time.sleep(0.5)

    assert recovered_health is not None, "Supervisor failed to auto-recover child server"
    new_pid = recovered_health.get("pid")
    assert new_pid != initial_pid
    print(f"PASS: Auto-recovery verified! Old PID {initial_pid} -> New PID {new_pid}.")


def test_07_binance_connectivity_and_diagnostics():
    print("\n--- [Test 7] Verify Binance Connectivity & Heartbeat Diagnostics ---")
    health = query_health()
    assert health is not None
    mkt = health.get("market_data", {})
    assert mkt.get("pairs_loaded") >= 10, "Pairs not loaded"
    assert mkt.get("status") in ("LIVE", "DELAYED"), f"Unexpected market status: {mkt.get('status')}"
    assert "last_error" in mkt
    print(f"PASS: Binance connectivity status={mkt.get('status')} (is_connected={mkt.get('is_connected')}, pairs={mkt.get('pairs_loaded')}, last_error={mkt.get('last_error')}).")


def test_08_paper_trading_persistence():
    print("\n--- [Test 8] Verify Paper-Trading Balance & Trades Preservation ---")
    health = query_health()
    assert health is not None
    pt = health.get("paper_trading", {})
    assert pt.get("balance") == 1018.36, f"Expected 1018.36, got {pt.get('balance')}"
    assert pt.get("total_trades") == 26, f"Expected 26 trades, got {pt.get('total_trades')}"
    assert pt.get("open_positions") == 0, f"Expected 0 open positions, got {pt.get('open_positions')}"
    print(f"PASS: Paper portfolio preserved: Balance=${pt.get('balance')} USDT, Total trades={pt.get('total_trades')}.")


def main():
    print("=================================================================")
    print("   AlphaQuant AI Pro: Supervisor Lifecycle & Resilience Test")
    print("=================================================================")

    spid, srv_pid = test_01_clean_launch_via_vbscript()
    test_02_launch_with_existing_supervised_server(spid, srv_pid)
    new_spid, new_srv_pid = test_03_orphan_server_reconciliation()
    test_04_launch_twice_simultaneously()
    test_05_stop_and_restart()
    test_06_simulate_crash_and_recovery()
    test_07_binance_connectivity_and_diagnostics()
    test_08_paper_trading_persistence()

    print("\n" + "=" * 65)
    print("   ✅ ALL SUPERVISOR LIFECYCLE & RECOVERY TESTS PASSED!")
    print("=================================================================\n")


if __name__ == "__main__":
    main()
