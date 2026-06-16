"""
Eksekusi load test Locust dan monitoring HPA.
"""
import os
import sys
import subprocess
import time

from .config import (
    COMP_USERS, COMP_SPAWN, COMP_TIME,
    MODE_POD_DIRECT, MODE_SERVICE_L4, MODE_INGRESS_L7,
    RESULT_DIR, CSV_DIR, LOCUSTFILE, MANIFEST_FILE,
)
from .networking import (
    open_browser, verify_connection, free_port, get_external_ip,
    setup_pod_forward, setup_ingress_connection, resolve_l4_endpoint,
    _patch_svc_type, check_is_port_forward, POD_DIRECT_PORT,
    _unregister,
)
from .cluster import (
    run_quiet, setup_cluster, ensure_ingress,
    deploy_for_scenario,
)


# ============================================================
#  HPA MONITORING
# ============================================================

def monitor_hpa(log_path=None):
    """Pantau HPA selama pengujian dan catat ke file log."""
    if log_path is None:
        log_path = os.path.join(RESULT_DIR, "hpa_events.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    print(f"\n  [HPA Monitor] Mencatat event HPA ke {log_path}...")
    try:
        with open(log_path, "w") as f:
            f.write("=== HPA Scale Events ===\n")
        r = subprocess.run(
            ["kubectl", "get", "hpa", "moodle-hpa"],
            timeout=10, capture_output=True, text=True
        )
        if r.returncode == 0:
            with open(log_path, "a") as f:
                f.write(r.stdout + "\n")
            print(f"   HPA status awal:\n{r.stdout.strip()}")
        return True
    except Exception as e:
        print(f"   Peringatan: Gagal monitor HPA: {e}")
        return False


def log_hpa_scaling(log_path=None):
    """Log perubahan replika HPA."""
    if log_path is None:
        log_path = os.path.join(RESULT_DIR, "hpa_events.log")
    try:
        r = subprocess.run(
            ["kubectl", "get", "hpa", "moodle-hpa", "-o",
             "jsonpath={.status.currentReplicas}..{.status.desiredReplicas}"],
            timeout=5, capture_output=True, text=True
        )
        if r.stdout.strip():
            with open(log_path, "a") as f:
                f.write(f"  {r.stdout.strip()}\n")
    except Exception:
        pass


# ============================================================
#  LOCUST EXECUTION
# ============================================================

def run_locust_headless(host, users, spawn, run_time, html_path, csv_stem=None):
    """Jalankan Locust headless dan simpan hasil."""
    cmd = [
        "locust", "-f", LOCUSTFILE,
        f"--host={host}",
        f"--users={users}",
        f"--spawn-rate={spawn}",
        f"--run-time={run_time}",
        f"--html={html_path}",
        "--headless"
    ]
    if csv_stem:
        cmd.append(f"--csv={csv_stem}")
    subprocess.run(cmd, timeout=600)


def run_test(scenario, target_host, is_port_forward):
    """Jalankan Locust load test."""
    print()
    print("  FASE 5: Menjalankan Pengujian Beban")
    print()

    if scenario == "interaktif":
        print(f"\n  Membuka Locust Web UI dan halaman LMS...")
        print(f"  Target: {target_host}")
        print(f"  Locust UI: http://localhost:8089")
        open_browser(target_host)
        open_browser("http://localhost:8089")
        try:
            subprocess.run(["locust", "-f", LOCUSTFILE, f"--host={target_host}"])
        except KeyboardInterrupt:
            print("\n  Locust dihentikan.")
        return

    # Mode Headless
    scenario_label = "TANPA HPA" if scenario == "tanpa_hpa" else "DENGAN HPA"
    html_report = os.path.join(RESULT_DIR, f"{scenario}.html")

    # Default berdasarkan metode koneksi
    if is_port_forward:
        def_users, def_spawn, def_time = 150, 5, "5m"
        print(f"\n  Mode: Port-Forward (kapasitas terbatas)")
        print(f"  Default optimal: {def_users} users, spawn rate {def_spawn}/s")
    else:
        def_users, def_spawn, def_time = 500, 10, "5m"
        print(f"\n  Mode: Direct/Tunnel (throughput penuh)")
        print(f"  Default: {def_users} users, spawn rate {def_spawn}/s")

    # Input pengguna
    print(f"\n  --- Konfigurasi Beban (Skenario {scenario_label}) ---")
    try:
        inp = input(f"  Jumlah concurrent users (Default {def_users} user): ").strip()
        users = int(inp) if inp else def_users
    except ValueError:
        users = def_users

    try:
        inp = input(f"  Spawn rate/detik (Default {def_spawn}/s): ").strip()
        spawn_rate = int(inp) if inp else def_spawn
    except ValueError:
        spawn_rate = def_spawn

    try:
        inp = input(f"  Durasi pengujian (Default {def_time}): ").strip()
        run_time = inp if inp else def_time
    except Exception:
        run_time = def_time

    # Peringatan kapasitas
    if is_port_forward and users > 200:
        print(f"\n  Peringatan: {users} users mungkin terlalu tinggi untuk port-forward!")
        print(f"    Disarankan maks 200. Atau aktifkan tunnel di terminal terpisah:")
        print(f"    Buka PowerShell sebagai Admin, lalu jalankan: minikube tunnel")
        confirm = input("    Tetap lanjutkan? (y/n) [y]: ").strip().lower()
        if confirm == 'n':
            return

    # Jalankan Locust
    os.makedirs(RESULT_DIR, exist_ok=True)
    print(f"\n  Memulai pengujian: Skenario {scenario_label}")
    print(f"     Users: {users} | Spawn: {spawn_rate}/s | Durasi: {run_time}")
    print(f"     Target: {target_host}")
    print(f"     Laporan: {html_report}\n")

    try:
        run_locust_headless(target_host, users, spawn_rate, run_time, html_report)
        print(f"\n   Pengujian Skenario {scenario_label} selesai!")
        print(f"  File laporan: {os.path.abspath(html_report)}")
    except KeyboardInterrupt:
        print(f"\n  Peringatan: Pengujian dibatalkan.")


# ============================================================
#  SKENARIO C: PERBANDINGAN
# ============================================================

def run_comparison():
    """Auto-run 6 kombinasi skenario: 2 HPA × 3 LB mode."""
    # Lazy import to avoid circular dependency at module load
    from .reporter import generate_comparison_report, generate_cost_analysis

    print()
    print("  SKENARIO C: Perbandingan Load Balancing & Biaya")
    print()

    # Input konfigurasi beban untuk seluruh skenario
    print(f"\n  Konfigurasi Beban (Skenario Perbandingan)")
    try:
        inp = input(f"  Jumlah concurrent users (Default {COMP_USERS} user): ").strip()
        users = int(inp) if inp else COMP_USERS
    except ValueError:
        users = COMP_USERS
    try:
        inp = input(f"  Spawn rate/detik (Default {COMP_SPAWN}/s): ").strip()
        spawn_rate = int(inp) if inp else COMP_SPAWN
    except ValueError:
        spawn_rate = COMP_SPAWN
    try:
        inp = input(f"  Durasi pengujian (Default {COMP_TIME}): ").strip()
        run_time = inp if inp else COMP_TIME
    except Exception:
        run_time = COMP_TIME
    print(f"  Beban: {users} users / spawn {spawn_rate}/s / durasi {run_time}\n")

    if not setup_cluster():
        return

    ingress_ok = ensure_ingress()

    os.makedirs(RESULT_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)
    results = []

    modes = [MODE_POD_DIRECT, MODE_SERVICE_L4]
    if ingress_ok:
        modes.append(MODE_INGRESS_L7)

    # Deploy skenario pertama agar service muncul, baru cek tunnel
    print("\n  [Deploy Awal] Menyiapkan service untuk verifikasi koneksi...")
    deploy_for_scenario("tanpa_hpa")

    tunnel_ip = get_external_ip()
    if not tunnel_ip:
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  [WARNING] minikube tunnel TIDAK terdeteksi!    ║")
        print("  ║  Tanpa tunnel, data L4 & L7 TIDAK VALID        ║")
        print("  ║  untuk jurnal karena bottleneck port-forward.  ║")
        print("  ║                                                ║")
        print("  ║  Buka PowerShell sebagai Administrator:        ║")
        print("  ║    minikube tunnel                              ║")
        print("  ║  Lalu verifikasi:                               ║")
        print("  ║    kubectl get svc moodle-service               ║")
        print("  ║    (pastikan EXTERNAL-IP terisi)                ║")
        print("  ╚══════════════════════════════════════════════════╝")
        confirm = input("  Tetap lanjutkan tanpa tunnel? (y/n) [n]: ").strip().lower()
        if confirm != 'y':
            print("  Dibatalkan. Aktifkan tunnel dulu, lalu jalankan ulang.")
            return
        print("  Melanjutkan tanpa tunnel — data port-forward hanya untuk uji coba.\n")
    else:
        print(f"\n  [Tunnel] TERDETEKSI — External IP: {tunnel_ip}")
        print("  [Tunnel] Data L4 & L7 akan melalui tunnel — valid untuk jurnal!\n")

    for sc in ("tanpa_hpa", "dengan_hpa"):
        if sc == "dengan_hpa":
            deploy_for_scenario("dengan_hpa")

        for mode in modes:
            label = f"{mode} / {sc}"
            print(f"\n  -- [{label}] --")

            # Mulai monitor HPA untuk skenario Dengan HPA
            hpa_log = os.path.join(CSV_DIR, f"hpa_{mode}_{sc}_events.log") if sc == "dengan_hpa" else None
            if hpa_log:
                monitor_hpa(hpa_log)

            # Setup koneksi sesuai mode
            is_limited = True  # default: port-forward

            if mode == MODE_POD_DIRECT:
                if users > 100:
                    print(f"  [Peringatan] Direct Pod via port-forward: {users} users akan overload")
                    print(f"    Disarankan maks 100 user untuk mode ini")
                    confirm = input("    Tetap lanjutkan? (y/n) [y]: ").strip().lower()
                    if confirm == 'n':
                        print(f"   Skip {label}")
                        continue
                proc, host = setup_pod_forward()
                is_limited = True
            elif mode == MODE_INGRESS_L7:
                proc, host = setup_ingress_connection()
                is_limited = False
            else:
                proc, host, is_limited = resolve_l4_endpoint()

            if not host:
                print(f"   Gagal koneksi untuk {label}, skip")
                continue

            if not verify_connection(host, retries=5):
                print(f"   Verifikasi gagal untuk {label}, skip")
                if proc:
                    proc.terminate()
                    _unregister(proc)
                    free_port(POD_DIRECT_PORT)
                continue

            # Catat HPA sebelum test
            if hpa_log:
                log_hpa_scaling(hpa_log)

            # Run test
            html_path = os.path.join(RESULT_DIR, f"{mode}_{sc}.html")
            csv_stem = os.path.join(CSV_DIR, f"{mode}_{sc}")
            print(f"  Menjalankan load test ({users} users, {run_time})...")
            run_locust_headless(host, users, spawn_rate, run_time, html_path, csv_stem)

            # Catat HPA setelah test
            if hpa_log:
                log_hpa_scaling(hpa_log)

            results.append({
                "scenario": sc, "mode": mode,
                "html": html_path, "csv": csv_stem,
                "hpa_log": hpa_log,
                "is_limited": is_limited
            })
            if proc:
                proc.terminate()
                _unregister(proc)
            free_port(POD_DIRECT_PORT)
            # Setelah L7 test: kembalikan moodle-service ke LoadBalancer
            if mode == MODE_INGRESS_L7:
                print("  [L7] Mengembalikan moodle-service ke LoadBalancer...")
                _patch_svc_type("moodle-service", "LoadBalancer")
                time.sleep(3)

    # Generate laporan perbandingan & biaya
    any_limited = any(r.get("is_limited", True) for r in results)
    generate_comparison_report(results, users, run_time)
    generate_cost_analysis()

    print()
    print("   Selesai! Buka berkas berikut:")
    print(f"     - result/perbandingan.html")
    print(f"     - result/analisis_biaya.html")
    if any_limited:
        print()
        print("  [Peringatan] Beberapa mode menggunakan port-forward — data mungkin belum valid untuk jurnal.")
        print("  Pastikan minikube tunnel aktif untuk mendapatkan data penuh di mode L4 & L7.")
    print()
