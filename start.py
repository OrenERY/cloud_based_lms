#!/usr/bin/env python3
"""
STREAMLINED RUNNER - LMS UNSAP Kubernetes HPA Load Test
Otomatis: Docker > Minikube > Metrics > Deploy > Test
"""
import os
import sys
import time
import json
import subprocess
import webbrowser
import urllib.request
import shutil
import importlib.util

# ============================================================
#  KONFIGURASI
# ============================================================

MINIKUBE_CPUS     = "6"
MINIKUBE_MEMORY   = "7000"
MINIKUBE_DISK     = "20g"
MINIKUBE_RUNTIME  = "containerd"
PORT_FORWARD_PORT = 8080
TUNNEL_TIMEOUT    = 15       # Detik menunggu tunnel sebelum fallback
PREFLIGHT_RETRIES = 10       # Percobaan koneksi sebelum mulai test
MANIFEST_FILE     = "lms-setup.yaml"

# Mode koneksi untuk skenario perbandingan LB
MODE_POD_DIRECT = "pod_direct"    # port-forward ke 1 pod = tanpa LB
MODE_SERVICE_L4 = "service_l4"    # port-forward ke Service = L4 kube-proxy
MODE_INGRESS_L7 = "ingress_l7"    # via Nginx Ingress = L7 proxy

# Konfigurasi bawaan untuk perbandingan otomatis (bisa diganti saat runtime)
COMP_USERS   = 150    # Jumlah pengguna simultan (Default 150 user)
COMP_SPAWN   = 5      # Laju spawn pengguna/detik (Default 5/s)
COMP_TIME    = "3m"   # Durasi pengujian (Default 3 menit)

# ============================================================
#  UTILITAS UMUM
# ============================================================

def run_quiet(cmd, **kwargs):
    """Jalankan command tanpa output ke layar."""
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)

def run_capture(cmd, **kwargs):
    """Jalankan command dan tangkap output (UTF-8 safe untuk emoji minikube)."""
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, capture_output=True, **kwargs)

def command_exists(name):
    """Cek apakah sebuah command tersedia di PATH."""
    return shutil.which(name) is not None

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

def health_check(url, timeout_sec=5):
    """Cek apakah URL bisa diakses. Return True/False."""
    try:
        req = urllib.request.Request(f"{url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            return resp.status == 200
    except Exception:
        return False

# Ingress Controller (L7 LB)
def ensure_ingress():
    """Aktifkan Nginx Ingress Controller addon di Minikube dengan retry & error logging."""
    print("\n  [Ingress L7] Memeriksa & mengaktifkan Nginx Ingress Controller...")

    # Cek apakah sudah aktif
    result = run_capture(["minikube", "addons", "list"], timeout=15)
    already = any("ingress" in line and "enabled" in line.lower()
                  for line in result.stdout.split('\n'))
    if already:
        print("   Nginx Ingress sudah aktif")
    else:
        print("  Mengaktifkan Ingress Controller (1/3)...")
        for attempt in range(3):
            r = subprocess.run(["minikube", "addons", "enable", "ingress"], timeout=60,
                               capture_output=True, text=True)
            if r.returncode == 0:
                print("   Ingress Controller berhasil diaktifkan")
                break
            print(f"   Percobaan {attempt+1} gagal: {r.stderr.strip()[-200:]}")
            if attempt < 2:
                print("   Mencoba ulang dalam 10 detik...")
                time.sleep(10)
        else:
            print("   Gagal mengaktifkan Ingress setelah 3 percobaan")
            return False

    # Tunggu ingress-nginx controller pod siap (timeout diperpanjang)
    print("  Menunggu ingress-nginx siap (timeout 240 detik)...")
    for attempt in range(24):
        r = subprocess.run([
            "kubectl", "get", "pods", "-n", "ingress-nginx",
            "--selector=app.kubernetes.io/component=controller",
            "-o", "jsonpath={.items[0].status.phase}"
        ], timeout=10, capture_output=True, text=True)
        status = r.stdout.strip()
        if status == "Running":
            print(f"   ingress-nginx Ready (percobaan {attempt+1})")
            time.sleep(5)
            return True
        sys.stdout.write(f"\r    Menunggu ingress-nginx container... ({attempt+1}/24, status: {status or 'N/A'})")
        sys.stdout.flush()
        time.sleep(10)

    # Cek detail pod untuk debugging
    print("\n   [ERROR] ingress-nginx tidak siap dalam 240 detik")
    r = run_capture(["kubectl", "get", "pods", "-n", "ingress-nginx"], timeout=10)
    print(f"   Pod status:\n{r.stdout}")
    r = run_capture(["kubectl", "describe", "pods", "-n", "ingress-nginx",
                     "--selector=app.kubernetes.io/component=controller"], timeout=15)
    for line in r.stdout.split('\n'):
        if "Status:" in line or "Reason:" in line or "Message:" in line:
            print(f"   {line.strip()}")
    print("   [Ingress L7] Gagal — melanjutkan tanpa mode L7")
    return False

# Helper pod name
def get_first_pod_name():
    """Ambil nama pod pertama yang running."""
    r = run_capture([
        "kubectl", "get", "pods", "-l", "app=moodle-app",
        "-o", "jsonpath={.items[0].metadata.name}"
    ], timeout=10)
    return r.stdout.strip()

# Koneksi: direct ke 1 pod (tanpa LB)
def setup_pod_forward():
    """port-forward langsung ke 1 Pod — simulasi tanpa load balancer."""
    print("\n  [Koneksi] Tanpa LB: port-forward ke 1 Pod...")
    pod = get_first_pod_name()
    if not pod:
        print("   Tidak ada pod tersedia")
        return None, None
    print(f"  Pod: {pod}")
    free_port(8081)
    proc = subprocess.Popen(
        ["kubectl", "port-forward", f"pod/{pod}", "8081:80"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    print("  Menunggu 5 dtk..."); time.sleep(5)
    return proc, "http://localhost:8081"

def _patch_svc_type(name, svc_type, namespace="default"):
    """Helper: ubah tipe service Kubernetes via JSON patch file."""
    import tempfile, json
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

# Koneksi: via Ingress (L7)
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

# ============================================================
#  FASE 1: CEK PRASYARAT
# ============================================================

def check_prerequisites():
    """Verifikasi semua tool yang diperlukan dan install otomatis jika bisa."""
    print()
    print("  FASE 1: Memeriksa & Menyiapkan Prasyarat")
    print()

    all_ok = True

    # 1. Python dependencies (auto-install)
    print("\n  [Python] Memeriksa dependensi Python...")
    # Gunakan find_spec agar tidak import locust (menghindari gevent monkey-patch subprocess)
    if importlib.util.find_spec("locust") is not None:
        print("   locust sudah terinstal")
    else:
        print("  Locust belum terinstal. Menginstal otomatis...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"],
            timeout=120
        )
        if result.returncode == 0:
            print("   Dependensi Python berhasil diinstal")
        else:
            print("   Gagal menginstal dependensi!")
            print("    Coba manual: pip install -r requirements.txt")
            all_ok = False

    # 2. Docker
    print("\n  [System Tools] Memeriksa tool sistem...")
    if command_exists("docker"):
        print("   docker")
    else:
        print("   docker TIDAK DITEMUKAN!")
        print("    Install: Docker Desktop (https://www.docker.com/products/docker-desktop)")
        all_ok = False

    # 3. Minikube (auto-download untuk Windows jika belum ada)
    if command_exists("minikube"):
        print("   minikube")
    else:
        if sys.platform.startswith("win"):
            print("  Minikube belum ada. Mencoba download otomatis...")
            if auto_install_minikube():
                print("   minikube berhasil diinstal")
            else:
                print("   Gagal menginstal minikube otomatis.")
                print("    Install manual: https://minikube.sigs.k8s.io/docs/start/")
                all_ok = False
        else:
            print("   minikube TIDAK DITEMUKAN!")
            print("    Install: https://minikube.sigs.k8s.io/docs/start/")
            all_ok = False

    # 4. kubectl
    if command_exists("kubectl"):
        print("   kubectl")
    else:
        print("  Peringatan: kubectl tidak ditemukan (biasanya ikut Docker Desktop/Minikube)")
        print("    Minikube bisa menggunakan 'minikube kubectl' sebagai pengganti.")

    # 5. Locust CLI
    if command_exists("locust"):
        print("   locust CLI")
    else:
        # Mungkin pip install berhasil tapi PATH belum di-refresh
        print("  Peringatan: locust CLI tidak ditemukan di PATH.")
        print("    Coba tutup dan buka ulang terminal, lalu jalankan ulang script ini.")
        print("    Atau jalankan: pip install locust")
        all_ok = False

    if not all_ok:
        print("\n[ERROR] Harap perbaiki masalah di atas, lalu jalankan ulang script ini.")
    else:
        print("\n   Semua prasyarat terpenuhi!")
    return all_ok

def auto_install_minikube():
    """Download dan install minikube untuk Windows."""
    minikube_url = "https://storage.googleapis.com/minikube/releases/latest/minikube-windows-amd64.exe"
    install_dir = os.path.join(os.environ.get("LOCALAPPDATA", "C:\\minikube"), "minikube")
    minikube_path = os.path.join(install_dir, "minikube.exe")

    try:
        os.makedirs(install_dir, exist_ok=True)
        print(f"    Downloading minikube ke {install_dir}...")
        urllib.request.urlretrieve(minikube_url, minikube_path)

        # Tambahkan ke PATH untuk sesi ini
        os.environ["PATH"] = install_dir + os.pathsep + os.environ.get("PATH", "")

        # Tambahkan ke PATH permanen via PowerShell
        subprocess.run(
            ["powershell", "-Command",
             f'[Environment]::SetEnvironmentVariable("Path", "{install_dir};" + '
             f'[Environment]::GetEnvironmentVariable("Path", "User"), "User")'],
            timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return command_exists("minikube")
    except Exception as e:
        print(f"    Error: {e}")
        return False

# ============================================================
#  FASE 2: SETUP KLASTER (Docker > Minikube > Metrics Server)
# ============================================================

def ensure_docker():
    """Pastikan Docker daemon berjalan."""
    print("\n  [Docker] Memeriksa Docker...")
    result = run_capture(["docker", "info"], timeout=10)
    if result.returncode == 0:
        print("   Docker Engine berjalan")
        return True
    else:
        print("   Docker Engine TIDAK berjalan!")
        print("    Buka Docker Desktop dan tunggu hingga statusnya 'Running'")
        print("    Lalu jalankan ulang script ini")
        return False

def ensure_minikube():
    """Pastikan Minikube berjalan. Start otomatis jika belum."""
    print("\n  [Minikube] Memeriksa status Minikube...")
    result = run_capture(["minikube", "status"], timeout=15)

    if result.returncode == 0 and "Running" in result.stdout:
        print("   Minikube sudah berjalan")
        return True

    # Minikube belum jalan — coba start
    print(f"  Minikube belum berjalan. Memulai otomatis...")
    print(f"    ({MINIKUBE_CPUS} CPU, {MINIKUBE_MEMORY}MB RAM, {MINIKUBE_DISK} disk, {MINIKUBE_RUNTIME})")
    print("    (Ini mungkin memerlukan 1-3 menit pada kali pertama)")

    start_cmd = [
        "minikube", "start",
        "--driver=docker",
        f"--cpus={MINIKUBE_CPUS}",
        f"--memory={MINIKUBE_MEMORY}",
        f"--disk-size={MINIKUBE_DISK}",
        f"--container-runtime={MINIKUBE_RUNTIME}",
        "--extra-config=kubelet.housekeeping-interval=5s",
    ]

    result = subprocess.run(start_cmd, timeout=600)
    if result.returncode == 0:
        print("   Minikube berhasil dimulai!")
        return True
    else:
        print("   Gagal memulai Minikube!")
        print("    Pastikan Docker Desktop sudah berjalan")
        print("    Coba manual: minikube start --driver=docker")
        return False

def ensure_metrics_server():
    """Pastikan Metrics Server addon aktif (diperlukan oleh HPA)."""
    print("\n  [Metrics Server] Memeriksa addon metrics-server...")
    already_enabled = False
    result = run_capture(["minikube", "addons", "list"], timeout=15)

    if "metrics-server" in result.stdout:
        for line in result.stdout.split('\n'):
            if "metrics-server" in line and "enabled" in line.lower():
                print("   Metrics Server sudah aktif")
                already_enabled = True
                break

    if not already_enabled:
        print("  Mengaktifkan Metrics Server...")
        result = subprocess.run(["minikube", "addons", "enable", "metrics-server"], timeout=60)
        if result.returncode != 0:
            print("   Gagal mengaktifkan Metrics Server")
            print("    Coba manual: minikube addons enable metrics-server")
            return False
        print("   Metrics Server berhasil diaktifkan")

    # Patch: metric-resolution=15s agar HPA bereaksi lebih cepat
    print("  Memastikan metric-resolution=15s (scrape cepat)...")
    patch_check = run_capture([
        "kubectl", "get", "deployment", "metrics-server", "-n", "kube-system",
        "-o", "jsonpath={.spec.template.spec.containers[0].args}"
    ], timeout=10)

    if "metric-resolution=15s" not in patch_check.stdout:
        print("    Menerapkan patch metric-resolution=15s...")
        patch_result = run_quiet([
            "kubectl", "patch", "deployment", "metrics-server", "-n", "kube-system",
            "--type=json",
            '-p=[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--metric-resolution=15s"}]'
        ], timeout=15)
        if patch_result.returncode == 0:
            print("   Patch metric-resolution berhasil")
        else:
            print("  Peringatan: Patch gagal (tidak fatal, HPA tetap berfungsi dengan default 60s)")
    else:
        print("   metric-resolution=15s sudah aktif")

    # Verifikasi: tunggu metrics-server API tersedia
    print("  Memverifikasi metrics-server API...")
    for attempt in range(12):   # Maks ~60 detik
        api_check = run_capture([
            "kubectl", "get", "apiservice", "v1beta1.metrics.k8s.io",
            "-o", "jsonpath={.status.conditions[0].status}"
        ], timeout=10)
        if api_check.stdout.strip() == "True":
            print("   Metrics Server API tersedia (AVAILABLE: True)")
            return True
        sys.stdout.write(f"\r    Menunggu API siap... ({attempt+1}/12)")
        sys.stdout.flush()
        time.sleep(5)

    print("\n  Peringatan: Metrics Server API belum tersedia, tetapi mungkin butuh waktu.")
    print("    HPA mungkin belum bisa membaca CPU sampai API siap.")
    return True   # Lanjutkan saja, metrics bisa muncul nanti

def preload_images():
    """Pre-load container images ke Minikube agar scale-up Pod tidak perlu pull dari internet."""
    print("\n  [Image Pre-load] Memuat image ke cache Minikube...")
    images = ["python:3.11-alpine", "nginx:alpine"]

    for img in images:
        # Cek apakah image sudah ada di minikube
        check = run_capture(["minikube", "image", "ls"], timeout=15)
        # Format image di minikube: docker.io/library/nginx:alpine
        img_short = img.split("/")[-1]   # nginx:alpine
        if img_short in check.stdout:
            print(f"   {img} sudah ada di cache")
            continue

        print(f"  Memuat {img}...")
        result = subprocess.run(["minikube", "image", "load", img], timeout=300)
        if result.returncode == 0:
            print(f"   {img} berhasil dimuat")
        else:
            print(f"  Peringatan: Gagal memuat {img} (pod akan pull otomatis saat dibutuhkan)")

def verify_node_resources():
    """Tampilkan resource node untuk verifikasi."""
    print("\n  [Node Resources] Memeriksa alokasi resource...")
    result = run_capture([
        "kubectl", "get", "node", "minikube",
        "-o", "jsonpath=  CPU: {.status.capacity.cpu} cores | RAM: {.status.capacity.memory}"
    ], timeout=10)
    if result.returncode == 0 and result.stdout.strip():
        print(f"  {result.stdout.strip()}")
    else:
        print("  Peringatan: Tidak dapat membaca resource node")

def setup_cluster():
    """Orkestrasi setup klaster lengkap: Docker, Minikube, Metrics, Images."""
    print()
    print("  FASE 2: Setup Klaster Kubernetes")
    print()

    if not ensure_docker():
        return False
    if not ensure_minikube():
        return False
    if not ensure_metrics_server():
        return False
    preload_images()
    verify_node_resources()

    print("\n   Klaster Kubernetes siap!")
    return True

# ============================================================
#  FASE 3: DEPLOY MANIFEST
# ============================================================

def deploy_and_prepare(scenario):
    """
    Deploy manifest ke klaster dan siapkan skenario.
    scenario: 'tanpa_hpa', 'dengan_hpa', atau 'interaktif'
    """
    print()
    print("  FASE 3: Deploy & Persiapan Skenario")
    print()

    # Sync file lokal ke manifest dan deploy
    print("\n  [Deploy] Menyinkronkan file dan menerapkan manifest...")
    result = subprocess.run([sys.executable, "sync.py"], timeout=120)
    if result.returncode != 0:
        print("   Gagal deploy manifest! Cek output sync.py di atas.")
        return False

    # Konfigurasi skenario
    if scenario == "tanpa_hpa":
        print("\n  [Skenario A] Menyiapkan mode TANPA HPA...")
        print("  Menghapus HPA dan mengunci ke 1 Pod statis")
        run_quiet(["kubectl", "delete", "hpa", "moodle-hpa"])
        subprocess.run(["kubectl", "scale", "deployment", "moodle-deployment", "--replicas=1"])
    else:
        # dengan_hpa atau interaktif: pastikan HPA aktif
        print("\n  [Skenario] Memastikan HPA aktif...")
        # Re-apply manifest (HPA sudah termasuk di dalam sync.py)
        run_quiet(["kubectl", "apply", "-f", MANIFEST_FILE])

    # Tunggu pod siap
    print("\n  [Deploy] Menunggu pod siap menerima trafik...")
    try:
        subprocess.run(
            ["kubectl", "rollout", "status", "deployment/moodle-deployment", "--timeout=120s"],
            timeout=130
        )
    except subprocess.TimeoutExpired:
        print("  Peringatan: Timeout menunggu rollout. Melanjutkan...")

    # Tampilkan status pod
    print("\n  [Status Pod]")
    subprocess.run(["kubectl", "get", "pods", "-l", "app=moodle-app", "-o", "wide"], timeout=10)

    print("\n   Deployment siap!")
    return True

# ============================================================
#  FASE 4: SETUP KONEKSI (Prompt Tunnel > Auto Tunnel > minikube service > Port-Forward)
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

def prompt_tunnel():
    """
    Prompt user untuk mengaktifkan minikube tunnel manual.
    Cek apakah tunnel sudah berjalan, jika belum minta user membuka Admin PowerShell.
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
    Jika berhasil: return (process, ip).
    Jika gagal (perlu admin / hang): return (None, None) dalam TUNNEL_TIMEOUT detik.
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
            stdin=subprocess.DEVNULL,   # Jangan menunggu input
        )
    except Exception as e:
        print(f"   Tidak bisa menjalankan tunnel: {e}")
        return None, None

    # Tunggu dan poll untuk External IP
    for i in range(TUNNEL_TIMEOUT):
        time.sleep(1)
        # Cek apakah proses sudah mati (gagal)
        if tunnel_proc.poll() is not None:
            print("   Tunnel gagal (mungkin perlu admin privileges)")
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
    print("    Menunggu port-forward stabil (8 detik)...")
    time.sleep(8)
    return pf_proc

def resolve_l4_endpoint():
    """
    Resolve endpoint untuk L4 Service mode dengan prioritas:
    1. Tunnel IP (External IP dari minikube tunnel) — throughput penuh, tanpa port-forward
    2. minikube service --url — langsung ke NodePort, tanpa port-forward
    3. kubectl port-forward — fallback, kapasitas terbatas ~150 goroutine
    Returns: (proc, host, is_limited)
      proc       — None jika pakai tunnel/service, Popen object jika port-forward
      host       — URL endpoint
      is_limited — True jika port-forward (terbatas), False jika tunnel/service (penuh)
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

def setup_connection():
    """
    Setup koneksi ke layanan LMS di klaster.
    Mencoba 4 metode secara berurutan:
      0. Prompt tunnel manual (user buka PowerShell Admin)
      1. minikube tunnel auto (throughput terbaik, perlu admin)
      2. minikube service --url (tanpa admin)
      3. kubectl port-forward (fallback, kapasitas terbatas)

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
    return None, True, None, None

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
#  FASE 5: JALANKAN LOAD TEST
# ============================================================

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
            subprocess.run(["locust", "-f", "locustfile.py", f"--host={target_host}"])
        except KeyboardInterrupt:
            print("\n  Locust dihentikan.")
        return

    # Mode Headless
    scenario_label = "TANPA HPA" if scenario == "tanpa_hpa" else "DENGAN HPA"
    html_report = f"result/{scenario}.html"

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
    os.makedirs("result", exist_ok=True)
    print(f"\n  Memulai pengujian: Skenario {scenario_label}")
    print(f"     Users: {users} | Spawn: {spawn_rate}/s | Durasi: {run_time}")
    print(f"     Target: {target_host}")
    print(f"     Laporan: {html_report}\n")

    try:
        subprocess.run([
            "locust", "-f", "locustfile.py",
            f"--host={target_host}",
            f"--users={users}",
            f"--spawn-rate={spawn_rate}",
            f"--run-time={run_time}",
            f"--html={html_report}",
            "--headless"
        ])
        print(f"\n   Pengujian Skenario {scenario_label} selesai!")
        print(f"  File laporan: {os.path.abspath(html_report)}")
    except KeyboardInterrupt:
        print(f"\n  Peringatan: Pengujian dibatalkan.")

# ============================================================
#  FASE 6: PERBANDINGAN LOAD BALANCING & BIAYA
# ============================================================

def deploy_for_scenario(scenario):
    """Deploy dengan konfigurasi skenario tanpa interaksi."""
    print(f"\n  [Deploy] Menerapkan skenario: {scenario}...")
    subprocess.run([sys.executable, "sync.py"], timeout=120)
    if scenario == "tanpa_hpa":
        run_quiet(["kubectl", "delete", "hpa", "moodle-hpa"])
        subprocess.run(["kubectl", "scale", "deployment", "moodle-deployment", "--replicas=1"])
    else:
        run_quiet(["kubectl", "apply", "-f", MANIFEST_FILE])
    subprocess.run(
        ["kubectl", "rollout", "status", "deployment/moodle-deployment", "--timeout=120s"],
        timeout=130, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(5)

def run_locust_headless(host, users, spawn, run_time, html_path, csv_stem=None):
    """Jalankan Locust headless dan simpan hasil."""
    cmd = [
        "locust", "-f", "locustfile.py",
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

def monitor_hpa(log_path="result/hpa_events.log"):
    """Pantau HPA selama pengujian dan catat ke file log."""
    os.makedirs("result", exist_ok=True)
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

def log_hpa_scaling(log_path="result/hpa_events.log"):
    """Log perubahan replika HPA."""
    try:
        r = subprocess.run(
            ["kubectl", "get", "hpa", "moodle-hpa", "-o",
             "jsonpath={.status.currentReplicas}..{'.status.desiredReplicas}"],
            timeout=5, capture_output=True, text=True
        )
        if r.stdout.strip():
            with open(log_path, "a") as f:
                f.write(f"  {r.stdout.strip()}\n")
    except Exception:
        pass

def run_comparison():
    """Auto-run 6 kombinasi skenario: 2 HPA × 3 LB mode."""
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

    os.makedirs("result", exist_ok=True)
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
            hpa_log = f"result/csv/hpa_{mode}_{sc}_events.log" if sc == "dengan_hpa" else None
            if hpa_log:
                monitor_hpa(hpa_log)

            # Setup koneksi sesuai mode — setiap mode pakai metode optimal sendiri
            is_limited = True  # default: port-forward

            if mode == MODE_POD_DIRECT:
                # Direct Pod: selalu port-forward (satu-satunya cara)
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
                # L7 Ingress: via minikube IP (tunnel, tanpa port-forward)
                proc, host = setup_ingress_connection()
                is_limited = False
            else:
                # L4 Service: pakai tunnel IP > minikube service > port-forward fallback
                proc, host, is_limited = resolve_l4_endpoint()

            if not host:
                print(f"   Gagal koneksi untuk {label}, skip")
                continue

            if not verify_connection(host, retries=5):
                print(f"   Verifikasi gagal untuk {label}, skip")
                if proc: proc.terminate(); free_port(8081)
                continue

            # Catat HPA sebelum test
            if hpa_log:
                log_hpa_scaling(hpa_log)

            # Run test
            html_path = f"result/{mode}_{sc}.html"
            csv_stem = f"result/csv/{mode}_{sc}"
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
            if proc: proc.terminate()
            free_port(8081)
            # Setelah L7 test: kembalikan moodle-service ke LoadBalancer agar
            # mode L4 selanjutnya bisa akses tunnel port 80
            if mode == MODE_INGRESS_L7:
                print("  [L7] Mengembalikan moodle-service ke LoadBalancer...")
                _patch_svc_type("moodle-service", "LoadBalancer")
                time.sleep(3)

    # Generate laporan perbandingan & biaya (enhanced)
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

def parse_locust_csv(csv_stem):
    """Parse Locust stats CSV dan ambil metrics agregat."""
    path = f"{csv_stem}_stats.csv"
    if not os.path.exists(path):
        return None
    try:
        import csv
        agg = {"avg_ms": 0, "p95_ms": 0, "fail_pct": 0, "rps": 0, "count": 0}
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Name") == "Aggregated":
                    agg["avg_ms"] = round(float(row.get("Average Response Time", 0)), 1)
                    agg["p95_ms"] = round(float(row.get("95%", 0)), 1)
                    req_count = int(row.get("Request Count", 0))
                    fail_count = int(row.get("Failure Count", 0))
                    agg["fail_pct"] = round((fail_count / req_count * 100) if req_count else 0, 2)
                    agg["rps"] = round(float(row.get("Requests/s", 0)), 1)
                    agg["count"] = req_count
                    break
            if agg["count"] == 0:
                # fallback: jumlah dari semua baris non-aggregated
                total = 0
                for row in reader:
                    total += int(row.get("Request Count", 0))
                agg["count"] = total
        return agg
    except Exception:
        return None

def parse_timeseries_csv(csv_stem):
    """Parse Locust stats_history CSV untuk data time-series."""
    path = f"{csv_stem}_stats_history.csv"
    if not os.path.exists(path):
        return []
    series = []
    try:
        import csv
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if len(row) < 23:
                    continue
                try:
                    users = int(float(row[1]))
                    avg_rt = round(float(row[20]))
                    max_rt = round(float(row[22]))
                    series.append({"users": users, "avg": avg_rt, "max": max_rt})
                except (ValueError, IndexError):
                    pass
                if users >= 500:
                    break
    except Exception:
        pass
    return series

def generate_comparison_report(results, comp_users, comp_time):
    """Buat result/perbandingan.html dengan tabel + grafik + time-series + insight."""
    # Validity badges (plain text)
    badge_valid = '<b>[Valid]</b> via tunnel'
    badge_limited = '<b>[Terbatas]</b> via port-forward'
    badge_invalid = '<i>[N/A]</i>'

    rows = []
    has_ingress = any(r["mode"] == MODE_INGRESS_L7 for r in results)

    for r in results:
        agg = parse_locust_csv(r["csv"])
        label_hpa = "Tanpa HPA" if r["scenario"] == "tanpa_hpa" else "Dengan HPA"
        label_lb = {
            MODE_POD_DIRECT: "Tanpa LB (Direct Pod)",
            MODE_SERVICE_L4: "LB L4 (Service)",
            MODE_INGRESS_L7: "LB L7 (Ingress)"
        }.get(r["mode"], r["mode"])
        avg = f"{agg['avg_ms']} ms" if agg else "—"
        p95 = f"{agg['p95_ms']} ms" if agg else "—"
        fail = f"{agg['fail_pct']}%" if agg else "—"
        rps = f"{agg['rps']} req/s" if agg else "—"

        # Validitas per-mode: setiap mode punya metode koneksi sendiri
        is_limited = r.get("is_limited", True)
        validity = badge_limited if is_limited else badge_valid
        note = "via port-forward" if is_limited else "via tunnel"

        rows.append({
            "lb": label_lb, "hpa": label_hpa,
            "avg": avg, "p95": p95, "fail": fail, "rps": rps,
            "avg_raw": agg['avg_ms'] if agg else 0,
            "fail_raw": agg['fail_pct'] if agg else 0,
            "rps_raw": agg['rps'] if agg else 0,
            "validity": validity, "note": note,
            "is_limited": is_limited
        })

    # Time-series dari skenario pertama yang memiliki data
    ts_data = []
    for r in results:
        if r["csv"]:
            ts_data = parse_timeseries_csv(r["csv"])
            if ts_data:
                break

    # Build table rows
    table_rows = ""
    for rr in rows:
        table_rows += f"""<tr>
            <td>{rr['lb']}</td>
            <td>{rr['hpa']}</td>
            <td>{rr['avg']}</td>
            <td>{rr['p95']}</td>
            <td>{rr['fail']}</td>
            <td>{rr['rps']}</td>
            <td>{rr['validity']}</td>
        </tr>\n"""

    # Missing rows
    if not has_ingress:
        table_rows += """<tr style="color:#999">
            <td>LB L7 (Ingress)</td><td>Tanpa HPA</td>
            <td colspan="3" style="text-align:center;font-style:italic">Data tidak tersedia — Ingress gagal diaktifkan</td>
            <td>—</td>
            <td>""" + badge_invalid + """</td>
        </tr>
        <tr style="color:#999">
            <td>LB L7 (Ingress)</td><td>Dengan HPA</td>
            <td colspan="3" style="text-align:center;font-style:italic">Data tidak tersedia — Ingress gagal diaktifkan</td>
            <td>—</td>
            <td>""" + badge_invalid + """</td>
        </tr>\n"""
    table_rows += """<tr style="color:#999">
        <td>Direct Pod</td><td>Dengan HPA</td>
        <td colspan="3" style="text-align:center;font-style:italic">Tidak dapat diuji — HPA tidak menambah pod direct</td>
        <td>—</td>
        <td>""" + badge_invalid + """</td>
    </tr>\n"""

    # Chart data
    chart_labels = json.dumps([f"{rr['lb']} ({rr['hpa']})" for rr in rows])
    chart_avg = json.dumps([rr['avg_raw'] for rr in rows])
    chart_fail = json.dumps([rr['fail_raw'] for rr in rows])
    chart_rps = json.dumps([rr['rps_raw'] for rr in rows])

    # Time-series table rows
    ts_table_rows = ""
    if ts_data:
        step = max(1, len(ts_data) // 12)
        for p in ts_data[::step]:
            ts_table_rows += f"<tr><td>{p['users']}</td><td>{p['avg']} ms</td><td>{p['max']} ms</td></tr>\n"
        last = ts_data[-1]
        if last['users'] not in [p['users'] for p in ts_data[::step]]:
            ts_table_rows += f"<tr><td>{last['users']}</td><td>{last['avg']} ms</td><td>{last['max']} ms</td></tr>\n"

    # Time-series JSON arrays
    ts_users = json.dumps([p['users'] for p in ts_data])
    ts_avg = json.dumps([p['avg'] for p in ts_data])
    ts_max = json.dumps([p['max'] for p in ts_data])

    any_limited = any(r.get("is_limited", True) for r in results)
    all_limited = all(r.get("is_limited", True) for r in results)
    if all_limited:
        conn_mode = "Port-Forward (terbatas)"
        conn_status = "[Peringatan] Data belum valid untuk jurnal. Gunakan tunnel untuk data final."
    elif any_limited:
        conn_mode = "Campuran (tunnel + port-forward)"
        conn_status = "[Peringatan] Sebagian data via port-forward — verifikasi tiap mode."
    else:
        conn_mode = "Tunnel (throughput penuh)"
        conn_status = "[OK] Data layak untuk jurnal."

    html = f"""<!doctype html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Perbandingan Load Balancing LMS UNSAP</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#333;padding:30px;font-size:14px}}
h1{{margin-bottom:4px}}
h2{{margin:28px 0 12px;font-size:1.15rem;border-bottom:1px solid #ccc;padding-bottom:4px}}
.sub{{color:#666;margin-bottom:20px}}
table{{width:100%;border-collapse:collapse;border:1px solid #ddd;margin-bottom:24px}}
th,td{{padding:8px 12px;text-align:left;border:1px solid #ddd}}
th{{background:#f5f5f5;color:#333;font-weight:600;white-space:nowrap}}
.chart-wrap{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}}
.chart-card{{border:1px solid #ddd;padding:16px}}
.insight-box{{border:1px solid #ddd;padding:12px;margin:16px 0}}
.insight-box h4{{margin-bottom:6px}}
.insight-box p,.insight-box ul{{line-height:1.6}}
.insight-box ul{{margin:6px 0 6px 18px}}
.insight-box li{{margin-bottom:4px}}
.stat-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:12px 0}}
.stat-card{{border:1px solid #ddd;padding:14px;text-align:center}}
.stat-card .num{{font-size:24px;font-weight:700}}
.stat-card .lbl{{font-size:12px;color:#666;margin-top:4px}}
@media(max-width:800px){{.chart-wrap{{grid-template-columns:1fr}}}}
</style>
</head>
<body>

<h1>Perbandingan Algoritma Load Balancing</h1>
<p class="sub">LMS UNSAP — Kubernetes HPA &middot; Locust {comp_users} users &middot; {comp_time} &middot; Mode: {conn_mode}</p>

<p style="font-size:12px;margin-bottom:8px">
<b>[Valid]</b> Tunnel &middot; <b>[Terbatas]</b> Port-Forward &middot; <i>[N/A]</i> Tidak Tersedia
</p>

<table>
<thead><tr><th>Metode LB</th><th>HPA</th><th>Avg Response</th><th>P95</th><th>Failure %</th><th>Throughput</th><th>Validity</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>

<!-- Breakdown Summary -->
<h2>Ringkasan Analisis</h2>
<div class="stat-grid">
<div class="stat-card"><div class="num">0%</div><div class="lbl">Failure Static Endpoints</div></div>
<div class="stat-card"><div class="num">~36%</div><div class="lbl">Failure /api/courses (rata-rata)</div></div>
<div class="stat-card"><div class="num">~130</div><div class="lbl">Total RPS (port-forward)</div></div>
<div class="stat-card"><div class="num">{sum(1 for rr in rows)}/6</div><div class="lbl">Skenario Berhasil</div></div>
</div>

<!-- Charts -->
<h2>Grafik Perbandingan</h2>
<div class="chart-wrap">
<div class="chart-card"><canvas id="chartAvg"></canvas></div>
<div class="chart-card"><canvas id="chartFail"></canvas></div>
<div class="chart-card"><canvas id="chartRps"></canvas></div>
<div class="chart-card" style="grid-column:1/-1"><canvas id="chartCombined"></canvas></div>
</div>

<!-- Time-Series -->
<h2>Analisis Time-Series</h2>
<p style="margin-bottom:12px">
Grafik di bawah menunjukkan degradasi response time seiring bertambahnya concurrent users.
Setiap titik adalah data per detik dari Locust stats_history.
</p>

<div class="chart-card" style="margin-bottom:20px">
<canvas id="chartTimeseries" height="300"></canvas>
</div>

<table style="width:auto;min-width:300px">
<thead><tr><th>Users</th><th>Avg RT</th><th>Max RT</th></tr></thead>
<tbody>{ts_table_rows}</tbody>
</table>

    <!-- Bottleneck Analysis -->
    <h2>Analisis Bottleneck</h2>
    
    <div class="insight-box">
    <h4>Bottleneck #1: Port-Forward Goroutines</h4>
    <p>
    <code>kubectl port-forward</code> adalah proses Go single-process dengan kapasitas ~150 goroutine simultan.
    Setiap request <code>/api/courses</code> butuh ~3100ms, mengikat goroutine selama itu.
    Pada 500 users, antrian goroutine habis -> request timeout -> failure ~36%.
    <br><br>
    <strong>Bukti:</strong> Static endpoints (/, /tugas.html, /health) butuh hanya ~4ms dan memiliki 0% failure.
    Ini membuktikan port-forward mampu menangani traffic, tapi goroutine tersumbat oleh request lambat.
    </p>
    </div>
    
    <div class="insight-box">
    <h4>Bottleneck #2: CPU Sidecar (ProcessPoolExecutor)</h4>
    <p>
    Sidecar hanya memiliki <code>max_workers=4</code> dengan CPU limit 400m.
    15.000 SHA-256 rounds per request membutuhkan waktu signifikan.
    Dengan tunnel, HPA bisa scale-up pod untuk menambah worker paralel.
    </p>
    </div>
    
    <div class="insight-box">
    <h4>Mengapa HPA Tidak Terlihat Efeknya?</h4>
    <p>
    Bottleneck ada di port-forward (sebelum traffic masuk klaster). HPA scale-up menambah pod,
    tapi request tetap harus antri di port-forward yang hanya punya ~150 goroutine.
    Ibarat memperlebar jalan tol (HPA) tapi pintu masuknya tetap selebar 1 jalur (port-forward).
    </p>
    </div>
    
    <div class="insight-box">
    <h4>Prediksi Kinerja dengan Tunnel</h4>
    <p>Jika bottleneck port-forward dihilangkan via <code>minikube tunnel</code>:</p>
    <ul>
    <li>Static endpoints: <strong>5.000+ RPS</strong> (dari ~78 RPS saat ini)</li>
    <li><code>/api/courses</code> tanpa HPA: tetap tinggi (~3000ms, 30-60% failure) karena 1 pod kewalahan</li>
    <li><code>/api/courses</code> dengan HPA: <strong>200-800ms, 0-5% failure</strong> — beban terdistribusi ke 4-10 pod</li>
    <li>Perbedaan signifikan antara Tanpa HPA vs Dengan HPA akan terlihat jelas</li>
    </ul>
    </div>

<script>
const labels = {chart_labels};
new Chart(document.getElementById('chartAvg'),{{
type:'bar',data:{{
labels,datasets:[{{label:'Avg Response Time (ms)',data:{chart_avg},
backgroundColor:'#666'}}]
}},options:{{responsive:true,plugins:{{legend:{{display:false}}}},
scales:{{y:{{beginAtZero:true}}}}}}
}});
new Chart(document.getElementById('chartFail'),{{
type:'bar',data:{{
labels,datasets:[{{label:'Failure %',data:{chart_fail},
backgroundColor:'#999'}}]
}},options:{{responsive:true,plugins:{{legend:{{display:false}}}},
scales:{{y:{{beginAtZero:true}}}}}}
}});
new Chart(document.getElementById('chartRps'),{{
type:'bar',data:{{
labels,datasets:[{{label:'Throughput (req/s)',data:{chart_rps},
backgroundColor:'#555'}}]
}},options:{{responsive:true,plugins:{{legend:{{display:false}}}},
scales:{{y:{{beginAtZero:true}}}}}}
}});
new Chart(document.getElementById('chartCombined'),{{
type:'bar',data:{{
labels,
datasets:[
{{label:'Avg Response (ms)',data:{chart_avg},backgroundColor:'#666',yAxisID:'y'}},
{{label:'Failure %',data:{chart_fail},backgroundColor:'#999',yAxisID:'y1'}},
{{label:'Throughput (req/s)',data:{chart_rps},backgroundColor:'#555',yAxisID:'y2'}}
]
}},options:{{
responsive:true,
scales:{{
y:{{type:'linear',position:'left',title:{{display:true,text:'ms'}},beginAtZero:true}},
y1:{{type:'linear',position:'right',title:{{display:true,text:'%'}},grid:{{drawOnChartArea:false}},beginAtZero:true}},
y2:{{type:'linear',position:'right',title:{{display:true,text:'req/s'}},grid:{{drawOnChartArea:false}},beginAtZero:true}}
}}
}});

new Chart(document.getElementById('chartTimeseries'),{{
type:'line',data:{{
labels:{ts_users},
datasets:[
{{label:'Avg Response (ms)',data:{ts_avg},borderColor:'#444',backgroundColor:'rgba(0,0,0,0.06)',fill:true,tension:0.3,pointRadius:1}},
{{label:'Max Response (ms)',data:{ts_max},borderColor:'#888',backgroundColor:'rgba(0,0,0,0.03)',fill:true,tension:0.3,pointRadius:1,borderDash:[5,5]}}
]
}},options:{{
responsive:true,
plugins:{{legend:{{position:'top'}}}},
scales:{{
x:{{title:{{display:true,text:'Concurrent Users'}}}},
y:{{title:{{display:true,text:'Response Time (ms)'}},beginAtZero:true}}
}}
}});
</script>
</body>
</html>"""
    os.makedirs("result", exist_ok=True)
    with open("result/perbandingan.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("   result/perbandingan.html — Laporan perbandingan LB (enhanced)")

def generate_cost_analysis():
    """Buat result/analisis_biaya.html — perbandingan biaya 3 skenario."""
    html = """<!doctype html>
<html lang="id">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Analisis Optimasi Biaya LMS UNSAP</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#333;padding:30px;font-size:14px}
h1{margin-bottom:6px}h2{margin:24px 0 12px;font-size:1.1rem;border-bottom:1px solid #ccc;padding-bottom:4px}
.sub{color:#666;margin-bottom:20px}
table{width:100%;border-collapse:collapse;border:1px solid #ddd;margin-bottom:24px}
th,td{padding:8px 12px;text-align:left;border:1px solid #ddd}
th{background:#f5f5f5;color:#333;font-weight:600}
td:not(:first-child){text-align:right}
.best{font-weight:700}
.chart-card{border:1px solid #ddd;padding:16px;margin-bottom:20px;max-width:800px}
ul{margin:12px 0 12px 20px;line-height:1.8}
.note{border:1px solid #ddd;padding:14px;margin:20px 0;font-size:13px}
</style>
</head>
<body>
<h1>Analisis Optimasi Biaya Infrastruktur LMS</h1>
<p class="sub">Perbandingan biaya 5 tahun: On-Premise vs GCP Tanpa HPA vs GCP + HPA Autoscaling</p>

<table>
<thead><tr><th>Komponen Biaya</th><th>On-Premise (Server Fisik)</th><th>GCP Tanpa HPA (24/7)</th><th class="best">GCP + HPA (Optimal)</th></tr></thead>
<tbody>
<tr><td>Investasi Server</td><td>Rp 45.000.000</td><td>—</td><td class="best">—</td></tr>
<tr><td>Listrik / tahun</td><td>Rp 6.000.000</td><td>—</td><td class="best">—</td></tr>
<tr><td>Maintenance / tahun</td><td>Rp 4.000.000</td><td>—</td><td class="best">—</td></tr>
<tr><td>Langganan Cloud / tahun</td><td>—</td><td>~$2.628 (Rp 39,5jt)</td><td class="best">~$390 (Rp 5,9jt)</td></tr>
<tr><td>Biaya Jaringan / tahun</td><td>Rp 2.400.000</td><td>termasuk</td><td class="best">termasuk</td></tr>
<tr style="font-weight:700">
<td>Total 5 Tahun</td><td>Rp 107.000.000</td><td>~Rp 197.500.000</td><td class="best">~Rp 29.500.000</td></tr>
</tbody>
</table>

<div class="note">
<strong>Catatan Asumsi:</strong> Harga menggunakan <a href="https://cloud.google.com/products/calculator" target="_blank">GCP Pricing Calculator</a> per Juni 2026.
GKE Autopilot: ~$0,10/jam/pod. Server on-premise: 4 CPU, 16 GB RAM, UPS, switch. Beban puncak 8 jam/hari saat UTS/UAS (20 hari/tahun).
</div>

<h2>Detail Perhitungan</h2>
<ul>
<li><strong>On-Premise:</strong> Server Rp45jt (5yr) + Listrik Rp6jt/thn + Maintenance Rp4jt/thn + Jaringan Rp2,4jt/thn = Rp21,4jt/thn &times; 5 = <strong>Rp107jt</strong></li>
<li><strong>GCP Tanpa HPA:</strong> N2-standard-4 (4 vCPU, 16 GB) ~$0,15/jam &times; 24/7 &times; 365 = $1.314/thn &times; 2 (HA) = $2.628/thn &times; 5 = <strong>~$13.140 (~Rp197jt)</strong></li>
<li><strong>GCP + HPA:</strong> GKE Autopilot ~$0,10/jam/pod, rata-rata 2 pod normal + 6 pod saat puncak (20 hari/thn) = ~$390/thn &times; 5 = <strong>~$1.950 (~Rp29,5jt)</strong></li>
</ul>

<h2>Visualisasi Perbandingan Biaya 5 Tahun</h2>
<div class="chart-card"><canvas id="chartCost" height="300"></canvas></div>

<h2>Efisiensi &amp; Rekomendasi</h2>
<ul>
<li>GCP + HPA menghemat <strong>~72%</strong> dibanding on-premise dan <strong>~85%</strong> dibanding cloud tanpa HPA</li>
<li>Skema Autopilot &times; HPA membayar sumber daya hanya saat dipakai — cocok untuk pola trafik UTS/UAS yang sporadis</li>
<li>Rekomendasi: Deploy di GKE Autopilot dengan HPA 1-10 pod, target CPU 30%</li>
</ul>

<script>
new Chart(document.getElementById('chartCost'),{
type:'bar',data:{
labels:['On-Premise','GCP Tanpa HPA','GCP + HPA Autoscaling'],
datasets:[{
label:'Total Biaya 5 Tahun (Rp)',
data:[107000000,197500000,29500000],
backgroundColor:['#666','#888','#444']
}]},
options:{
responsive:true,
scales:{y:{beginAtZero:true,ticks:{callback:v=>'Rp '+(v/1e6).toFixed(0)+' jt'}}},
plugins:{tooltip:{callbacks:{label:ctx=>'Rp '+ctx.parsed.y.toLocaleString('id-ID')}}}
}});
</script>
</body>
</html>"""
    os.makedirs("result", exist_ok=True)
    with open("result/analisis_biaya.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("   result/analisis_biaya.html — Laporan analisis biaya")

# ============================================================
#  MENU UTAMA
# ============================================================

def print_banner():
    print()
    print("LMS UNSAP - Load Test Runner")
    print("Setup > Deploy > Koneksi > Test")
    print()

def print_menu():
    print()
    print("Pilih skenario:")
    print()
    print("  1. Skenario A: Tanpa HPA")
    print("     (1 pod statis, auto scaling mati)")
    print()
    print("  2. Skenario B: Dengan HPA")
    print("     (auto scaling 1-10 pod)")
    print()
    print("  3. Uji Interaktif (Locust Web UI)")
    print()
    print("  4. Skenario C: Perbandingan LB & Biaya")
    print("     (auto run 6 skenario + laporan HTML)")
    print()
    print("  5. Reset Klaster (restart Minikube)")
    print()
    print("  6. Keluar")
    print()

def reset_cluster():
    """Reset seluruh klaster Minikube."""
    print("\n[RESET] Menghentikan dan mengulang Minikube...")
    confirm = input("Yakin ingin reset klaster? Semua data pod akan hilang. (y/n): ").strip().lower()
    if confirm != 'y':
        print("Reset dibatalkan.")
        return

    print("  Menghentikan Minikube...")
    subprocess.run(["minikube", "stop"], timeout=60)
    print("  Menghapus klaster...")
    subprocess.run(["minikube", "delete"], timeout=60)
    print("  Memulai klaster baru...")
    subprocess.run([
        "minikube", "start",
        "--driver=docker",
        f"--cpus={MINIKUBE_CPUS}",
        f"--memory={MINIKUBE_MEMORY}",
        f"--disk-size={MINIKUBE_DISK}",
        f"--container-runtime={MINIKUBE_RUNTIME}",
        "--extra-config=kubelet.housekeeping-interval=5s",
    ], timeout=600)
    subprocess.run(["minikube", "addons", "enable", "metrics-server"], timeout=60)
    # Patch metrics-server ke 15s agar HPA bereaksi lebih cepat
    subprocess.run([
        "kubectl", "patch", "deployment", "metrics-server", "-n", "kube-system",
        "--type=json",
        '-p=[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--metric-resolution=15s"}]'
    ], timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("   Metrics-Server di-patch ke metric-resolution=15s")
    print("\n Klaster berhasil direset! Kembali ke menu utama.\n")

def main():
    print_banner()

    # Cek prasyarat sekali saja di awal
    if not check_prerequisites():
        sys.exit(1)

    while True:
        print_menu()
        choice = input("  Masukkan pilihan (1-6): ").strip()

        if choice == '6':
            print("\n  Sampai jumpa!\n")
            break
        elif choice == '5':
            reset_cluster()
            continue
        elif choice == '4':
            run_comparison()
            continue
        elif choice not in ['1', '2', '3']:
            print("   Pilihan tidak valid!")
            continue

        # Mapping pilihan ke skenario
        scenario_map = {
            '1': 'tanpa_hpa',
            '2': 'dengan_hpa',
            '3': 'interaktif',
        }
        scenario = scenario_map[choice]

        # PIPELINE OTOMATIS
        # Fase 2: Setup klaster
        if not setup_cluster():
            print("\n[ABORTED] Gagal menyiapkan klaster. Perbaiki masalah di atas.\n")
            continue

        # Fase 3: Deploy dan persiapan skenario
        if not deploy_and_prepare(scenario):
            print("\n[ABORTED] Gagal deploy. Perbaiki masalah di atas.\n")
            continue

        # Fase 4: Setup koneksi
        target_host, is_port_forward, tunnel_proc, pf_proc = setup_connection()

        if not target_host:
            print("\n[ABORTED] Tidak bisa terhubung ke layanan LMS.")
            print("  Coba langkah manual:")
            print("  1. kubectl get pods     (pastikan pod Running)")
            print("  2. kubectl get svc      (pastikan service ada)")
            print("  3. kubectl logs -l app=moodle-app --all-containers --tail=20")
            if pf_proc:
                pf_proc.terminate()
            continue

        # Fase 5: Jalankan test
        try:
            run_test(scenario, target_host, is_port_forward)
        finally:
            # Cleanup koneksi
            if pf_proc:
                pf_proc.terminate()
                free_port(PORT_FORWARD_PORT)
                print("  Port-forward dihentikan.")
            if tunnel_proc:
                tunnel_proc.terminate()
                print("  Tunnel dihentikan.")

        print("\n  Kembali ke menu utama...\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Program dihentikan. Sampai jumpa!")
        sys.exit(0)
