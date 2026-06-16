"""
Orkestrasi klaster Kubernetes lokal: Docker, Minikube, Metrics Server, Image Pre-load.
"""
import sys
import subprocess
import time

from .config import (
    MINIKUBE_CPUS, MINIKUBE_MEMORY, MINIKUBE_DISK, MINIKUBE_RUNTIME,
    MANIFEST_FILE,
)


def run_quiet(cmd, **kwargs):
    """Jalankan command tanpa output ke layar."""
    return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def run_capture(cmd, **kwargs):
    """Jalankan command dan tangkap output (UTF-8 safe untuk emoji minikube)."""
    kwargs.setdefault("encoding", "utf-8")
    kwargs.setdefault("errors", "replace")
    return subprocess.run(cmd, capture_output=True, **kwargs)


# ============================================================
#  DOCKER
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


# ============================================================
#  MINIKUBE
# ============================================================

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


# ============================================================
#  METRICS SERVER
# ============================================================

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


# ============================================================
#  INGRESS CONTROLLER
# ============================================================

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


# ============================================================
#  IMAGE PRE-LOAD & VERIFICATION
# ============================================================

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


# ============================================================
#  DEPLOYMENT
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
    result = subprocess.run([sys.executable, "sync.py"], timeout=120,
                            cwd=os.path.dirname(MANIFEST_FILE))
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


def deploy_for_scenario(scenario):
    """Deploy dengan konfigurasi skenario tanpa interaksi (untuk Skenario C)."""
    print(f"\n  [Deploy] Menerapkan skenario: {scenario}...")
    subprocess.run([sys.executable, "sync.py"], timeout=120,
                   cwd=os.path.dirname(MANIFEST_FILE))
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


# ============================================================
#  CLUSTER RESET
# ============================================================

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


# ============================================================
#  COMPOSITE: FULL SETUP
# ============================================================

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


# Need os for deploy paths
import os
