"""
Pemeriksaan prasyarat sistem: Docker, Minikube, kubectl, Locust, Python deps.
"""
import os
import sys
import subprocess
import shutil
import importlib.util
import urllib.request


def command_exists(name):
    """Cek apakah sebuah command tersedia di PATH."""
    return shutil.which(name) is not None


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
