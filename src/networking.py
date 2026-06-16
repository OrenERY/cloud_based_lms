"""
Manajemen koneksi jaringan: port-forward, tunnel, ingress, dan lifecycle proses.
Menggunakan atexit untuk membersihkan proses zombie secara otomatis.
"""
import os
import sys
import json
import time
import atexit
import socket
import subprocess
import webbrowser
import urllib.request
import tempfile

from .config import (
    PORT_FORWARD_PORT, POD_DIRECT_PORT, TUNNEL_TIMEOUT, PREFLIGHT_RETRIES,
    MODE_POD_DIRECT, MODE_SERVICE_L4, MODE_INGRESS_L7,
)
from .cluster import run_quiet, run_capture


# ============================================================
#  PROCESS REGISTRY (Zombie Prevention)
# ============================================================

_active_processes = []


def _cleanup_all():
    """Kill semua proses background yang masih berjalan saat script exit."""
    for proc in _active_processes:
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            pass
    _active_processes.clear()


atexit.register(_cleanup_all)


def _register(proc):
    """Daftarkan proses ke registry untuk cleanup otomatis."""
    if proc is not None:
        _active_processes.append(proc)
    return proc


def _unregister(proc):
    """Hapus proses dari registry (sudah di-terminate manual)."""
    if proc in _active_processes:
        _active_processes.remove(proc)


# ============================================================
#  PORT UTILITIES
# ============================================================

def find_free_port(start_port=8080):
    """Cari port lokal yang tersedia mulai dari start_port."""
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except socket.error:
                port += 1
    raise IOError("Tidak ada port yang tersedia.")


def free_port(port):
    """Bebaskan port yang sedang digunakan (Windows & Linux/WSL)."""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.check_output("netstat -ano", shell=True).decode()
            for line in out.strip().split('\n'):
                if f":{port}" in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        run_quiet(f"taskkill /F /PID {pid}", shell=True)
        else:
            try:
                out = subprocess.check_output(f"lsof -t -i:{port}", shell=True).decode().strip()
                for pid in out.split():
                    if pid.isdigit():
                        subprocess.run(f"kill -9 {pid}", shell=True)
            except Exception:
                run_quiet(f"fuser -k {port}/tcp", shell=True)
    except Exception:
        pass


# ============================================================
#  HEALTH CHECK & BROWSER
# ============================================================

def health_check(url, timeout_sec=5):
    """Cek apakah URL bisa diakses. Return True/False."""
    try:
        req = urllib.request.Request(f"{url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return resp.status == 200
    except Exception:
        return False


def open_browser(url):
    """Buka URL di browser (support Windows, WSL2, Linux)."""
    try:
        is_wsl = False
        if sys.platform.startswith("linux"):
            try:
                with open("/proc/version", "r") as f:
                    if "microsoft" in f.read().lower():
                        is_wsl = True
            except Exception:
                pass
        if is_wsl:
            safe_url = url.replace("&", "^&")
            run_quiet(f'cmd.exe /c start "" "{safe_url}"', shell=True)
        else:
            webbrowser.open(url)
    except Exception:
        pass


def verify_connection(url, retries=None):
    """Verifikasi koneksi ke layanan LMS."""
    retries = retries or PREFLIGHT_RETRIES
    print(f"\n  [Verifikasi] Menguji koneksi ke {url} ...")
    for attempt in range(1, retries + 1):
        if health_check(url):
            print(f"   Koneksi OK! (percobaan {attempt})")
            return True
        sys.stdout.write(f"\r   Percobaan {attempt}/{retries}...")
        sys.stdout.flush()
        if attempt < retries:
            time.sleep(3)
    print(f"\n   Gagal terhubung ke {url} setelah {retries} percobaan")
    return False


# ============================================================
#  EXTERNAL IP (TUNNEL DETECTION)
# ============================================================

def get_external_ip():
    """
    Cek External IP dari minikube tunnel via service LoadBalancer.
    Di Windows, IP biasanya 127.0.0.1 — itu normal untuk tunnel.
    Returns IP string jika ada, None jika tidak.
    """
    try:
        ip = subprocess.check_output(
            ["kubectl", "get", "svc", "moodle-service", "-o",
             "jsonpath={.status.loadBalancer.ingress[0].ip}"],
            timeout=5
        ).decode().strip()
        if ip:
            return ip
    except Exception:
        pass
    return None


def check_is_port_forward():
    """Deteksi apakah koneksi saat ini via port-forward."""
    try:
        r = subprocess.run(
            ["kubectl", "get", "svc", "moodle-service", "-o",
             "jsonpath={.status.loadBalancer.ingress[0].ip}"],
            timeout=5, capture_output=True, text=True
        )
        if r.stdout.strip():
            return False  # Tunnel aktif, punya External IP
    except Exception:
        pass
    return True  # Fallback: anggap port-forward


# ============================================================
#  SERVICE TYPE PATCHING
# ============================================================

def _patch_svc_type(name, svc_type, namespace="default"):
    """Helper: ubah tipe service Kubernetes via JSON patch file."""
    pf = os.path.join(tempfile.gettempdir(), f"patch_{name}.json")
    try:
        with open(pf, "w") as f:
            json.dump({"spec": {"type": svc_type}}, f)
        subprocess.run(
            ["kubectl", "patch", "svc", name, "-n", namespace, "--patch-file", pf],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass


# ============================================================
#  CONNECTION METHODS
# ============================================================

def get_first_pod_name():
    """Ambil nama pod pertama yang running."""
    r = run_capture([
        "kubectl", "get", "pods", "-l", "app=moodle-app",
        "-o", "jsonpath={.items[0].metadata.name}"
    ], timeout=10)
    return r.stdout.strip()


def setup_pod_forward():
    """port-forward langsung ke 1 Pod — simulasi tanpa load balancer."""
    print("\n  [Koneksi] Tanpa LB: port-forward ke 1 Pod...")
    pod = get_first_pod_name()
    if not pod:
        print("   Tidak ada pod tersedia")
        return None, None
    print(f"  Pod: {pod}")
    free_port(POD_DIRECT_PORT)
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod}", f"{POD_DIRECT_PORT}:80"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    _register(proc)
    print("  Menunggu 5 dtk..."); time.sleep(5)
    return proc, f"http://localhost:{POD_DIRECT_PORT}"


def setup_ingress_connection():
    """
    Koneksi ke Nginx Ingress Controller (L7).
    Di Windows, Minikube IP tidak reachable langsung, jadi:
      1. Moodle-service dialihkan ke ClusterIP (bebaskan port 80 tunnel)
      2. Ingress Controller dipatch ke LoadBalancer
      3. Akses via 127.0.0.1:80 (tunnel → Ingress Controller → Ingress resource → service)
    """
    print("\n  [Koneksi] L7 LB: Nginx Ingress...")

    # Step 1: pastikan ingress-nginx-controller adalah LoadBalancer
    print("  Memastikan Ingress Controller sebagai LoadBalancer...")
    _patch_svc_type("ingress-nginx-controller", "LoadBalancer", "ingress-nginx")
    time.sleep(3)

    # Step 2: alihkan moodle-service ke ClusterIP agar tidak rebutan port 80 tunnel
    print("  Mengalihkan moodle-service ke ClusterIP (bebaskan port 80)...")
    _patch_svc_type("moodle-service", "ClusterIP")
    time.sleep(3)

    # Step 3: polling tunnel (127.0.0.1:80) untuk Ingress Controller
    print("  Polling 127.0.0.1:80 untuk Ingress Controller...")
    for attempt in range(10):
        if health_check("http://127.0.0.1"):
            print(f"   Ingress siap via tunnel! (percobaan {attempt+1})")
            return None, "http://127.0.0.1"
        sys.stdout.write(f"\r    Menunggu ingress via tunnel... ({attempt+1}/10)")
        sys.stdout.flush()
        time.sleep(3)

    print("\n   [ERROR] Gagal terhubung ke Ingress Controller via tunnel")
    print("   [L7 Ingress] Tidak tersedia — melanjutkan tanpa mode L7")
    return None, None


def start_port_forward():
    """Start kubectl port-forward sebagai fallback terakhir."""
    print(f"\n  [Koneksi] Menggunakan port-forward (localhost:{PORT_FORWARD_PORT})...")
    print("  Peringatan: Mode ini memiliki kapasitas terbatas (~150 koneksi simultan)")

    free_port(PORT_FORWARD_PORT)
    pf_proc = subprocess.Popen(
        ["kubectl", "port-forward", "svc/moodle-service", f"{PORT_FORWARD_PORT}:80"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    _register(pf_proc)
    print("    Menunggu port-forward stabil (8 detik)...")
    time.sleep(8)
    return pf_proc


def resolve_l4_endpoint():
    """
    Resolve endpoint untuk L4 Service mode dengan prioritas:
    1. Tunnel IP (External IP dari minikube tunnel)
    2. minikube service --url
    3. kubectl port-forward (fallback)
    Returns: (proc, host, is_limited)
    """
    ip = get_external_ip()
    if ip:
        print(f"   [L4] Tunnel aktif — External IP: {ip}")
        host = f"http://{ip}"
        if verify_connection(host, retries=3):
            return None, host, False
        print("   [L4] Tunnel IP ditemukan tapi koneksi gagal, coba metode lain...")

    print("   [L4] Tunnel tidak tersedia, mencoba minikube service...")
    svc_url = try_minikube_service()
    if svc_url:
        print(f"   [L4] Via minikube service: {svc_url}")
        return None, svc_url, False

    print("   [L4] Fallback ke port-forward (kapasitas ~150 goroutine)")
    proc = start_port_forward()
    return proc, f"http://localhost:{PORT_FORWARD_PORT}", True


# ============================================================
#  TUNNEL
# ============================================================

def prompt_tunnel():
    """
    Prompt user untuk mengaktifkan minikube tunnel manual.
    Returns: True jika tunnel aktif, False jika user pilih port-forward.
    """
    ip = get_external_ip()
    if ip:
        print("  [Tunnel] Terdeteksi aktif.")
        return True

    print()
    print("  ===========================================================")
    print("  [Tunnel] minikube tunnel BELUM terdeteksi!")
    print("  Untuk throughput penuh, tunnel WAJIB diaktifkan.")
    print()
    print("  Langkah:")
    print("   1. Buka PowerShell sebagai Administrator")
    print("   2. Jalankan: minikube tunnel")
    print("   3. Biarkan proses tunnel berjalan (jangan ditutup)")
    print("  ===========================================================")
    input("  Setelah tunnel berjalan, tekan Enter untuk melanjutkan...")

    ip = get_external_ip()
    if ip:
        print("  [Tunnel] Terdeteksi aktif!")
        return True

    choice = input("  Tunnel masih belum terdeteksi. Lanjut dengan port-forward? (y/n) [n]: ").strip().lower()
    return choice == 'y'


def try_tunnel():
    """
    Coba jalankan minikube tunnel secara non-blocking.
    Returns: (process, ip) atau (None, None).
    """
    print("\n  [Koneksi] Mencoba minikube tunnel (untuk throughput maksimal)...")

    # Bersihkan rute lama yang mungkin mengganggu
    try:
        run_quiet(["minikube", "tunnel", "--cleanup"], timeout=10)
    except subprocess.TimeoutExpired:
        print("  Peringatan: Cleanup tunnel timeout (diabaikan, melanjutkan...)")
    time.sleep(1)

    # Cek apakah tunnel sudah berjalan di terminal lain
    existing_ip = get_external_ip()
    if existing_ip:
        print(f"   Tunnel sudah aktif! External IP: {existing_ip}")
        return None, existing_ip   # None process = tidak perlu cleanup

    # Coba jalankan tunnel baru di background
    print("    (Jika muncul popup izin admin/UAC, klik 'Yes')")
    try:
        tunnel_proc = subprocess.Popen(
            ["minikube", "tunnel"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        _register(tunnel_proc)
    except Exception as e:
        print(f"   Tidak bisa menjalankan tunnel: {e}")
        return None, None

    # Tunggu dan poll untuk External IP
    for i in range(TUNNEL_TIMEOUT):
        time.sleep(1)
        # Cek apakah proses sudah mati (gagal)
        if tunnel_proc.poll() is not None:
            print("   Tunnel gagal (mungkin perlu admin privileges)")
            _unregister(tunnel_proc)
            return None, None
        # Cek External IP
        ip = get_external_ip()
        if ip:
            print(f"   Tunnel aktif! External IP: {ip} (dalam {i+1} detik)")
            return tunnel_proc, ip
        sys.stdout.write(f"\r    Menunggu tunnel... ({i+1}/{TUNNEL_TIMEOUT}s)")
        sys.stdout.flush()

    print(f"\n   Tunnel tidak merespon dalam {TUNNEL_TIMEOUT} detik")
    tunnel_proc.terminate()
    _unregister(tunnel_proc)
    return None, None


def try_minikube_service():
    """Coba dapatkan URL via minikube service --url."""
    print("\n  [Koneksi] Mencoba minikube service --url...")
    try:
        result = run_capture(
            ["minikube", "service", "moodle-service", "--url"],
            timeout=20
        )
        url = result.stdout.strip().split('\n')[0].strip()
        if url.startswith("http"):
            print(f"   URL didapatkan: {url}")
            return url
    except Exception:
        pass
    print("   minikube service tidak tersedia")
    return None


# ============================================================
#  COMPOSITE: FULL CONNECTION SETUP (Skenario A/B/3)
# ============================================================

def setup_connection():
    """
    Setup koneksi ke layanan LMS di klaster.
    Mencoba 4 metode secara berurutan:
      0. Prompt tunnel manual
      1. minikube tunnel auto
      2. minikube service --url
      3. kubectl port-forward (fallback)

    Returns: (target_host, is_port_forward, tunnel_proc, pf_proc)
    """
    print()
    print("  FASE 4: Menyiapkan Koneksi ke Layanan")
    print()

    tunnel_proc = None
    pf_proc = None

    # Metode 0: Prompt tunnel manual
    if prompt_tunnel():
        ip = get_external_ip()
        if ip:
            target_host = f"http://{ip}"
            if verify_connection(target_host, retries=3):
                return target_host, False, None, None
            print("  Peringatan: IP tunnel ditemukan tapi koneksi gagal.")

    # Metode 1: Auto tunnel
    tunnel_proc, tunnel_ip = try_tunnel()
    if tunnel_ip:
        target_host = f"http://{tunnel_ip}"
        # Verifikasi
        if verify_connection(target_host):
            return target_host, False, tunnel_proc, None
        else:
            print("  Peringatan: Tunnel IP ditemukan tapi koneksi gagal. Mencoba metode lain...")
            if tunnel_proc:
                tunnel_proc.terminate()
                _unregister(tunnel_proc)

    # Metode 2: minikube service
    svc_url = try_minikube_service()
    if svc_url:
        if verify_connection(svc_url):
            return svc_url, False, None, None

    # Metode 3: Port-Forward
    pf_proc = start_port_forward()
    target_host = f"http://localhost:{PORT_FORWARD_PORT}"
    if verify_connection(target_host):
        return target_host, True, None, pf_proc

    # Semua metode gagal
    if pf_proc:
        pf_proc.terminate()
        _unregister(pf_proc)
    return None, True, None, None


def cleanup_connection(tunnel_proc, pf_proc):
    """Bersihkan proses koneksi secara manual."""
    if pf_proc:
        pf_proc.terminate()
        _unregister(pf_proc)
        free_port(PORT_FORWARD_PORT)
        print("  Port-forward dihentikan.")
    if tunnel_proc:
        tunnel_proc.terminate()
        _unregister(tunnel_proc)
        print("  Tunnel dihentikan.")
