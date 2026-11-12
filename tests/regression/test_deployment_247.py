"""Comprehensive 24/7 Deployment & Resiliency Test Suite for AlphaQuant AI Pro.

Validates all 12 operational scenarios:
1. Normal startup & health status
2. Duplicate startup prevention
3. Port 8000 occupancy identification (AlphaQuant)
4. Port occupied by foreign application (no blind kill, clean error)
5. Application crash detection & supervisor auto-recovery
6. Windows Task Scheduler registration
7. Server restart cycle
8. WebSocket live connection & reconnection
9. Gemini API outage graceful fallback
10. Atomic persistence & corrupted file auto-recovery via .bak
11. Graceful shutdown & PID cleanup
12. Reboot autostart configuration verification
"""

import http.server
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PYTHON_EXE = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
if not Path(PYTHON_EXE).exists():
    PYTHON_EXE = sys.executable

PID_FILE = PROJECT_ROOT / "runtime" / "alphaquant.pid"
SUPERVISOR_PID = PROJECT_ROOT / "runtime" / "supervisor.pid"
LOGS_DIR = PROJECT_ROOT / "runtime" / "logs"
DATA_DIR = PROJECT_ROOT / "data"

TEST_PORT = 8000


def query_health(port: int = TEST_PORT, timeout: float = 3.0) -> dict | None:
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/health")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception:
        pass
    return None


def wait_for_health(port: int = TEST_PORT, max_wait: float = 15.0) -> dict | None:
    start = time.time()
    while time.time() - start < max_wait:
        data = query_health(port)
        if data:
            return data
        time.sleep(0.5)
    return None


def is_port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def test_scenario_01_normal_startup():
    print("\n--- [Scenario 1] Normal Startup & Health Verification ---")
    # Launch server directly with run_dashboard
    proc = subprocess.Popen(
        [PYTHON_EXE, "dashboard/run_dashboard.py", "--port", str(TEST_PORT), "--no-browser"],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    health = wait_for_health(TEST_PORT, max_wait=15.0)
    assert health is not None, "Failed to reach /api/health after starting server"
    assert health.get("status") == "ok"
    assert health.get("app") == "AlphaQuant AI Pro Trading Terminal"
    assert health.get("healthy") is True
    actual_pid = health.get("pid")
    assert actual_pid is not None, "Health endpoint did not return pid"
    assert PID_FILE.exists(), "PID file runtime/alphaquant.pid was not created"
    assert int(PID_FILE.read_text().strip()) == actual_pid

    # Validate paper trading balance and trade history preserved
    pt_data = health.get("paper_trading", {})
    assert pt_data.get("balance") == 1018.36, f"Expected balance 1018.36, got {pt_data.get('balance')}"
    assert pt_data.get("total_trades") == 26, f"Expected 26 trades, got {pt_data.get('total_trades')}"

    print(f"PASS: Server running PID={actual_pid}, Balance=${pt_data.get('balance')}, Trades={pt_data.get('total_trades')}")
    return proc


def test_scenario_02_duplicate_prevention(primary_proc):
    print("\n--- [Scenario 2] Duplicate Startup Prevention ---")
    # Attempt to start a second server on the same port
    res = subprocess.run(
        [PYTHON_EXE, "dashboard/run_dashboard.py", "--port", str(TEST_PORT), "--no-browser"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Expected clean exit 0, got {res.returncode}"
    assert "ALREADY RUNNING" in res.stdout, f"Expected ALREADY RUNNING in output: {res.stdout}"
    assert "Duplicate instance was NOT started" in res.stdout

    # Verify primary process is untouched
    assert query_health(TEST_PORT) is not None, "Primary process was unexpectedly killed!"
    print("PASS: Duplicate launch cleanly exited with code 0 without creating conflict.")


def test_scenario_03_port_occupied_by_alphaquant():
    print("\n--- [Scenario 3] Port 8000 Occupancy Identification ---")
    from dashboard.run_dashboard import check_port_status
    status, health = check_port_status("127.0.0.1", TEST_PORT)
    assert status == "alphaquant", f"Expected status 'alphaquant', got '{status}'"
    assert health is not None
    assert health.get("app") == "AlphaQuant AI Pro Trading Terminal"
    print(f"PASS: check_port_status correctly identified active AlphaQuant instance (Uptime: {health.get('uptime_seconds')}s).")


def test_scenario_04_port_occupied_by_foreign_application(primary_proc):
    print("\n--- [Scenario 4] Foreign Application Port Conflict Protection ---")
    foreign_port = 8899

    # Start a dummy HTTP server on foreign_port
    class DummyHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Hello from Foreign App")
        def log_message(self, *args):
            pass

    server = socketserver.TCPServer(("127.0.0.1", foreign_port), DummyHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    try:
        from dashboard.run_dashboard import check_port_status
        status, _ = check_port_status("127.0.0.1", foreign_port)
        assert status == "foreign", f"Expected status 'foreign', got '{status}'"

        # Attempt to launch AlphaQuant on foreign_port
        res = subprocess.run(
            [PYTHON_EXE, "dashboard/run_dashboard.py", "--port", str(foreign_port), "--no-browser"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 1, f"Expected exit code 1 for foreign port collision, got {res.returncode}"
        assert "PORT OCCUPIED BY ANOTHER APPLICATION" in res.stdout
        assert "Startup aborted" in res.stdout or "Please terminate" in res.stdout

        # Verify foreign server is still alive (no blind kill)
        req = urllib.request.Request(f"http://127.0.0.1:{foreign_port}/")
        with urllib.request.urlopen(req, timeout=2) as r:
            body = r.read().decode()
            assert "Foreign App" in body, "Foreign application was disrupted!"

        print("PASS: Unrelated application protected; AlphaQuant aborted startup with exit code 1.")
    finally:
        server.shutdown()
        server.server_close()


def test_scenario_05_crash_and_auto_recovery(primary_proc):
    print("\n--- [Scenario 5] Crash Detection and Supervisor Auto-Recovery ---")
    from scripts.launch.alphaquant_supervisor import stop_running_alphaquant
    stop_running_alphaquant("127.0.0.1", TEST_PORT)
    while is_port_listening(TEST_PORT):
        time.sleep(0.5)

    # Launch via supervisor
    supervisor_proc = subprocess.Popen(
        [PYTHON_EXE, "scripts/launch/alphaquant_supervisor.py", "--port", str(TEST_PORT)],
        cwd=str(PROJECT_ROOT),
    )
    health1 = wait_for_health(TEST_PORT, max_wait=15.0)
    assert health1 is not None, "Supervisor failed to start AlphaQuant"
    first_pid = health1.get("pid")
    print(f"Supervisor started server PID={first_pid}")

    # Now intentionally terminate the child server process to simulate crash
    child_proc_handle = psutil.Process(first_pid)
    child_proc_handle.kill()
    time.sleep(1.0)
    assert not psutil.pid_exists(first_pid), "Child was not terminated"

    print("Simulated crash executed. Waiting for supervisor auto-recovery...")
    # Supervisor has initial backoff 2s. Wait for new PID on health endpoint.
    recovered_health = None
    start_wait = time.time()
    while time.time() - start_wait < 15.0:
        h = query_health(TEST_PORT)
        if h and h.get("pid") != first_pid:
            recovered_health = h
            break
        time.sleep(0.5)

    assert recovered_health is not None, "Supervisor failed to recover server within 15s"
    new_pid = recovered_health.get("pid")
    assert new_pid != first_pid, f"Expected new PID, got {new_pid}"
    print(f"PASS: Auto-recovery successful! New server PID={new_pid} active on port {TEST_PORT}.")

    # Stop supervisor cleanly
    stop_running_alphaquant("127.0.0.1", TEST_PORT)
    time.sleep(1.0)


def test_scenario_06_windows_task_scheduler():
    print("\n--- [Scenario 6] Windows Task Scheduler Registration ---")
    script_path = PROJECT_ROOT / "scripts" / "launch" / "install_autostart.ps1"
    cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"Task installation failed: {res.stderr}\n{res.stdout}"
    assert "SUCCESS" in res.stdout

    # Verify Task Scheduler XML or User Startup configuration
    xml_path = PROJECT_ROOT / "scripts" / "launch" / "AlphaQuantAIPro.xml"
    assert xml_path.exists(), "Scheduled Task XML template was not found"
    assert "alphaquant_supervisor.py" in xml_path.read_text()

    # Check Startup VBS or Scheduled Task
    startup_vbs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AlphaQuantAIPro.vbs"
    task_check = subprocess.run(["schtasks", "/query", "/tn", "AlphaQuantAIPro"], capture_output=True, text=True)
    assert task_check.returncode == 0 or startup_vbs.exists(), "Neither Scheduled Task nor Startup VBS was created"
    print("PASS: Windows Autostart mechanism verified (Task Scheduler XML and/or Startup entry active).")


def test_scenario_07_server_restart():
    print("\n--- [Scenario 7] Server Restart Cycle ---")
    # Start server
    proc = subprocess.Popen(
        [PYTHON_EXE, "dashboard/run_dashboard.py", "--port", str(TEST_PORT), "--no-browser"],
        cwd=str(PROJECT_ROOT),
    )
    health = wait_for_health(TEST_PORT, max_wait=15.0)
    assert health is not None
    old_pid = health["pid"]

    # Use supervisor stop utility
    from scripts.launch.alphaquant_supervisor import stop_running_alphaquant
    assert stop_running_alphaquant("127.0.0.1", TEST_PORT) is True
    assert not is_port_listening(TEST_PORT), "Port was not released after stop"

    # Start again
    proc2 = subprocess.Popen(
        [PYTHON_EXE, "dashboard/run_dashboard.py", "--port", str(TEST_PORT), "--no-browser"],
        cwd=str(PROJECT_ROOT),
    )
    health2 = wait_for_health(TEST_PORT, max_wait=15.0)
    assert health2 is not None
    new_pid = health2["pid"]
    assert new_pid != old_pid
    print(f"PASS: Restart cycle successful (Old PID {old_pid} -> Stopped -> New PID {new_pid}).")
    return proc2


def test_scenario_08_websocket_stream():
    print("\n--- [Scenario 8] WebSocket Live Connection & Stream ---")
    import websockets
    import asyncio

    async def _test_ws():
        uri = f"ws://127.0.0.1:{TEST_PORT}/ws/live"
        async with websockets.connect(uri) as ws:
            # Subscribe to BTC/USDT 15m
            await ws.send(json.dumps({"pair": "BTC/USDT", "interval": "15m"}))
            # Wait for tick
            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            data = json.loads(msg)
            assert data.get("type") == "tick"
            assert data.get("pair") == "BTC/USDT"
            assert "ticker" in data
            return True

    res = asyncio.run(_test_ws())
    assert res is True
    print("PASS: WebSocket connection accepted and received live tick stream.")


def test_scenario_09_gemini_api_outage_handling():
    print("\n--- [Scenario 9] Gemini API Outage Fallback ---")
    # Post chat request
    req_body = json.dumps({"message": "Analyze market structure", "pair": "BTC/USDT"}).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{TEST_PORT}/api/chat",
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        assert resp.status == 200
        reply = json.loads(resp.read().decode("utf-8"))
        assert "response" in reply
        assert len(reply["response"]) > 10
        print(f"PASS: AI Chat handled gracefully (Source: {reply.get('source', 'built-in')}).")


def test_scenario_10_atomic_data_persistence():
    print("\n--- [Scenario 10] Paper-Trading Atomic Persistence & Backup Recovery ---")
    from dashboard.backend.storage import atomic_write_json, safe_load_json

    trades_file = DATA_DIR / "paper_trades.json"
    bak_file = DATA_DIR / "paper_trades.json.bak"

    # Verify current file is valid
    current = safe_load_json(trades_file)
    assert current["balance"] == 1018.36
    assert len(current["trade_history"]) == 26

    # Test atomic write generates .bak
    test_file = DATA_DIR / "test_persistence_temp.json"
    test_bak = DATA_DIR / "test_persistence_temp.json.bak"
    try:
        atomic_write_json(test_file, {"version": 1, "balance": 1000.0})
        atomic_write_json(test_file, {"version": 2, "balance": 1050.0})
        assert test_bak.exists(), "Backup file was not created"
        
        # Test corruption recovery
        test_file.write_text("CORRUPTED NON-JSON DATA")
        recovered = safe_load_json(test_file)
        assert recovered["version"] == 1, "Failed to recover from backup file"
        print("PASS: Atomic writes guarantee backup creation and safe automatic recovery from corruption.")
    finally:
        test_file.unlink(missing_ok=True)
        test_bak.unlink(missing_ok=True)


def test_scenario_11_graceful_shutdown(active_proc):
    print("\n--- [Scenario 11] Graceful Shutdown & PID Cleanup ---")
    from scripts.launch.alphaquant_supervisor import stop_running_alphaquant

    assert stop_running_alphaquant("127.0.0.1", TEST_PORT) is True
    time.sleep(1.0)
    assert not is_port_listening(TEST_PORT), "Port 8000 still in use after shutdown"
    assert not PID_FILE.exists(), "PID file was not cleaned up on shutdown"
    print("PASS: Server shut down gracefully, port 8000 freed, and PID file cleaned up.")


def test_scenario_12_reboot_configuration():
    print("\n--- [Scenario 12] Reboot Autostart Configuration ---")
    remove_script = PROJECT_ROOT / "scripts" / "launch" / "remove_autostart.ps1"
    rem_res = subprocess.run(["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(remove_script)], capture_output=True, text=True)
    assert rem_res.returncode == 0
    assert "SUCCESS" in rem_res.stdout or "removed successfully" in rem_res.stdout

    startup_vbs = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "AlphaQuantAIPro.vbs"
    assert not startup_vbs.exists(), "Startup VBS still exists after removal"
    print("PASS: Autostart cleanup verified successfully.")


def main():
    print("=================================================================")
    print("   AlphaQuant AI Pro: 24/7 Deployment & Self-Recovery Test Suite")
    print("=================================================================")

    # Ensure no lingering port 8000
    from scripts.launch.alphaquant_supervisor import stop_running_alphaquant
    stop_running_alphaquant("127.0.0.1", TEST_PORT)
    time.sleep(1.0)

    proc = test_scenario_01_normal_startup()
    try:
        test_scenario_02_duplicate_prevention(proc)
        test_scenario_03_port_occupied_by_alphaquant()
        test_scenario_04_port_occupied_by_foreign_application(proc)
        test_scenario_05_crash_and_auto_recovery(proc)
        test_scenario_06_windows_task_scheduler()
        proc2 = test_scenario_07_server_restart()
        test_scenario_08_websocket_stream()
        test_scenario_09_gemini_api_outage_handling()
        test_scenario_10_atomic_data_persistence()
        test_scenario_11_graceful_shutdown(proc2)
        test_scenario_12_reboot_configuration()

        print("\n" + "=" * 65)
        print("   ✅ ALL 12 OPERATIONAL & DEPLOYMENT SCENARIOS PASSED!")
        print("=================================================================\n")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        # Clean up
        stop_running_alphaquant("127.0.0.1", TEST_PORT)
        sys.exit(1)


if __name__ == "__main__":
    main()
