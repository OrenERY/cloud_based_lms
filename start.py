#!/usr/bin/env python3
"""
STREAMLINED RUNNER - LMS UNSAP Kubernetes HPA Load Test
Otomatis: Docker > Minikube > Metrics > Deploy > Test

Slim entrypoint — semua logika ada di src/ modules.
"""
import sys

from src.system_checks import check_prerequisites
from src.cluster import setup_cluster, deploy_and_prepare, reset_cluster
from src.networking import setup_connection, cleanup_connection, free_port
from src.config import PORT_FORWARD_PORT
from src.benchmark import run_test, run_comparison


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
            cleanup_connection(tunnel_proc, pf_proc)
            continue

        # Fase 5: Jalankan test
        try:
            run_test(scenario, target_host, is_port_forward)
        finally:
            # Cleanup koneksi
            cleanup_connection(tunnel_proc, pf_proc)

        print("\n  Kembali ke menu utama...\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Program dihentikan. Sampai jumpa!")
        sys.exit(0)