# LMS UNSAP – Kubernetes HPA & Load Testing Prototype

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Repository ini berisi prototipe arsitektur klaster Kubernetes untuk menguji elastisitas infrastruktur **LMS UNSAP** menggunakan **Horizontal Pod Autoscaler (HPA)**. Pengujian performa disimulasikan menggunakan **Locust** dengan **500 concurrent users** di bawah beban pengujian lokal.

---

## Spesifikasi Device Penelitian

| Komponen  | Spesifikasi                                      |
| --------- | ------------------------------------------------ |
| **OS**    | Windows 11 (bisa dijalankan langsung via PowerShell/WSL2) |
| **CPU**   | Intel Core i5-12450H (12th Gen)                  |
| **RAM**   | 16 GB DDR4                                       |
| **GPU**   | NVIDIA RTX 3050 Laptop                           |
| **Tools** | Docker Desktop, Minikube, kubectl, Python/Locust |

---

## Komponen & Prasyarat Sistem

1. **Docker Desktop** (dengan WSL 2 backend aktif).
2. **Minikube** (v1.x) sebagai orchestrator klaster Kubernetes lokal.
3. **Kubectl** untuk manajemen resource klaster.
4. **Python 3.x** & **Locust** untuk menjalankan skenario load testing.

---

## Struktur Berkas

| File                            | Deskripsi                                                                                                                                                                                 |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `index.html`                    | Halaman utama Dasbor LMS UNSAP (lokal)                                                                                                                                                    |
| `tugas.html`                    | Halaman pengumpulan tugas LMS UNSAP (lokal)                                                                                                                                               |
| `style.css`                     | Stylesheet utama tampilan LMS (lokal)                                                                                                                                                     |
| `sync.py`                       | Script otomatis untuk sinkronisasi file HTML/CSS ke manifes Kubernetes dan deploy ulang ke cluster (nginx tuning, sidecar stress, HPA, Ingress)                                           |
| `lms-setup.yaml`                | _Auto-generated_ — Manifes Kubernetes (dibuat oleh `sync.py`) berisi ConfigMaps, Deployment Nginx + Python sidecar, Service, Ingress, dan HPA                                              |
| `locustfile.py`                 | Script pengujian Locust untuk mensimulasikan trafik mahasiswa concurrent                                                                                                                  |
 | `start.py`                      | **Full Streamlined Runner** — Otomatisasi penuh: Docker -> Minikube -> Metrics Server -> Deploy -> Koneksi -> Load Test + Perbandingan LB & Biaya, semua dalam satu perintah `python start.py` |
| `requirements.txt`              | Dependensi Python (Locust)                                                                                                                                                                |
| `result/`                       | Folder laporan hasil pengujian (HTML report)                                                                                                                                              |
| `result/perbandingan.html`      | _Auto-generated_ — Laporan perbandingan Load Balancing (tabel + grafik Chart.js)                                                                                                           |
| `result/analisis_biaya.html`    | _Auto-generated_ — Laporan analisis optimasi biaya (On-Premise vs GCP vs GCP+HPA)                                                                                                         |

---

## Panduan Memulai (Setup & Deployment)

Anda dapat menjalankan seluruh proses ini langsung dari **Windows PowerShell** (direkomendasikan) atau dari dalam **WSL2**.

### Langkah 1: Persiapan Lingkungan & Dependensi (Sekali Saja)

Buka terminal (PowerShell atau WSL2) di folder proyek:

**Di Windows (PowerShell):**
```powershell
# Buat virtual environment
python -m venv venv
# Aktifkan virtual environment
.\venv\Scripts\activate
# Install dependensi
pip install -r requirements.txt
```

**Di Linux/WSL2:**
```bash
# Buat virtual environment
python3 -m venv venv
# Aktifkan virtual environment
source venv/bin/activate
# Install dependensi
pip install -r requirements.txt
```

---

### Langkah 2: Jalankan Streamlined Runner

Cukup jalankan **satu perintah** berikut — script akan **otomatis** menangani semuanya (Docker, Minikube, Metrics Server, Deploy, Koneksi, dan Load Test):

```bash
python start.py
```

> **INFO**: Script `start.py` akan otomatis:
> 1. Memverifikasi prasyarat (Docker, Minikube, kubectl, Locust)
> 2. Memulai Minikube jika belum berjalan (3 CPU, 4GB RAM)
> 3. Mengaktifkan Metrics Server untuk HPA
> 4. Deploy manifest terbaru ke klaster
> 5. Mencari koneksi terbaik (tunnel -> minikube service -> port-forward)
> 6. Menjalankan load test sesuai skenario pilihan
>
> **Tentang Tunnel**: Script akan otomatis mencoba `minikube tunnel`. Jika tunnel memerlukan hak Administrator dan gagal, script akan fallback ke port-forward dengan **150 users** (sudah cukup untuk memicu HPA dan menghasilkan data jurnal).
>
> **Opsional (untuk 500 users)**: Jika ingin throughput penuh, buka PowerShell terpisah sebagai **Administrator** dan jalankan `minikube tunnel` sebelum memulai `start.py`.

Anda akan disajikan menu interaktif:
```
╔══════════════════════════════════════════════════════╗
║   LMS UNSAP – Kubernetes HPA Load Test Runner       ║
║   Otomatis: Setup - Deploy - Koneksi - Test         ║
╚══════════════════════════════════════════════════════╝

┌────────────────────────────────────────────────────┐
│  Pilih Skenario Pengujian:                         │
│                                                    │
│  1. Skenario A: Tanpa HPA                          │
│     (1 Pod statis, tanpa autoscaling)               │
│                                                    │
│  2. Skenario B: Dengan HPA                         │
│     (Autoscaling 1-10 Pod)                          │
│                                                    │
│  3. Uji Interaktif (Locust Web UI)                 │
│                                                    │
│  4. Skenario C: Perbandingan LB & Biaya            │
│     (Auto-run 6 skenario + laporan HTML)            │
│                                                    │
│  5. Reset Klaster (Restart Minikube)               │
│                                                    │
│  6. Keluar                                         │
└────────────────────────────────────────────────────┘
```

#### Deskripsi Pilihan Pengujian:

* **Opsi 1: Skenario A: Tanpa HPA (Headless)**
  * **Alur Otomatis**: Cek Docker -> Start Minikube -> Enable Metrics -> Deploy manifest -> Hapus HPA -> Kunci 1 Pod -> Setup koneksi -> Load test -> Simpan `result/tanpa_hpa.html`.
  * **Tujuan**: Membuktikan server tunggal tradisional akan mengalami kelebihan beban (*high response times* & *failure rates*) saat lonjakan trafik terjadi.

* **Opsi 2: Skenario B: Dengan HPA (Headless)**
  * **Alur Otomatis**: Cek Docker -> Start Minikube -> Enable Metrics -> Deploy manifest + HPA -> Autoscaling (1-10 Pods) -> Setup koneksi -> Load test -> Simpan `result/dengan_hpa.html`.
  * **Tujuan**: Membuktikan keandalan autoscaling dalam membagi beban trafik secara otomatis ke pod-pod baru sehingga *failure rate* ditekan ke tingkat minimal/0%.

* **Opsi 3: Uji Interaktif (Locust Web UI)**
  * **Alur Otomatis**: Full setup -> Membuka LMS dan Locust Web UI (`http://localhost:8089`) di browser.
  * **Cara Menggunakan**: Masukkan parameter pengujian di Web UI lalu klik *Start swarming*.

* **Opsi 4: Skenario C: Perbandingan LB & Biaya**
  * **Alur Otomatis**: Setup cluster -> Enable Ingress -> Auto-run 6 kombinasi (3 mode LB x 2 mode HPA) dengan Locust headless (150 users, 3 menit) -> Generate laporan HTML:
    * **`result/perbandingan.html`** — Tabel perbandingan + grafik Chart.js (Avg Response, P95, Failure %, RPS)
    * **`result/analisis_biaya.html`** — Analisis biaya 5 tahun: On-Premise vs GCP tanpa HPA vs GCP + HPA
  * **Tujuan**: Membuktikan bahwa Layer 7 LB (Ingress) memberikan performa lebih stabil, dan Autoscaling menghemat biaya cloud secara signifikan.
  * **3 Mode Load Balancing yang Dibandingkan**:
    1. **Tanpa LB (Direct Pod)**: `port-forward` langsung ke 1 pod — simulasi server tunggal
    2. **LB L4 (Service)**: `port-forward` ke Service — kube-proxy round-robin (analogi GCP Network LB)
    3. **LB L7 (Ingress)**: via Nginx Ingress Controller — routing cerdas (analogi GCP HTTP(S) LB)

* **Opsi 5: Reset Klaster**
  * Menghentikan, menghapus, dan memulai ulang klaster Minikube dari awal.

---

## Arsitektur Pod & Optimasi HPA

Setiap Pod terdiri dari 2 container:

| Container          | Image                | Fungsi                                       | CPU Request | CPU Limit |
| ------------------ | -------------------- | -------------------------------------------- | ----------- | --------- |
| **moodle** (main)  | `nginx:alpine`       | Serving HTML/CSS + proxy ke stress endpoint  | 100m        | 300m      |
| **stress-sidecar** | `python:3.11-alpine` | CPU stress server (hashing loop 15k rounds)  | 200m        | 400m      |

### Detail Optimasi HPA (diatur dalam `sync.py`):
* **Beban CPU 15.000 Hashing Rounds dengan ProcessPoolExecutor**: Dioptimalkan menggunakan process pool agar terhindar dari Python GIL lock, dan disesuaikan ke 15.000 rounds agar throughput melonjak saat HPA melakukan scale-up.
* **HPA Maksimal 10 Pods**: Kapasitas replikasi dinaikkan hingga maksimal **10 Pod** untuk menampung beban tinggi 500 users.
* **Parallel Process-Pool Stress Server**: Container sidecar menggunakan `ProcessPoolExecutor` (multi-process) agar request diproses paralel di level proses tanpa terhambat Python GIL.
* **CPU Target 30%**: Ambang batas CPU diturunkan ke 30% agar HPA bereaksi lebih dini sebelum server mengalami kelebihan beban.
* **Scale-Up Agresif & Instan**: Mengatur `stabilizationWindowSeconds: 0` dan menaikkan penambahan Pod kebijakan scale-up menjadi **8 Pod sekaligus** agar replikasi cepat terjadi saat CPU melampaui target.
* **Scale-Down Cepat**: Mengatur `stabilizationWindowSeconds: 30` untuk mempermudah demonstrasi/pengujian lokal.
* **Readiness Probe Cepat**: Mengatur `initialDelaySeconds: 2` dan `periodSeconds: 2` agar pod baru terdeteksi siap melayani trafik dalam 2 detik.

---

## Cara Mengambil Data untuk Laporan Jurnal

Untuk analisis jurnal, data performa dan autoscaling dapat dikumpulkan dengan cara berikut:

1. **Log Transisi Autoscaling (Terminal)**:
   Saat pengujian Skenario B berjalan, buka terminal baru dan pantau aktivitas replika HPA secara real-time:
   ```bash
   kubectl get hpa moodle-hpa -w
   ```
   Catat waktu dan jumlah replika saat HPA melakukan scale-up (misalnya dari 1 pod menjadi 2 pod, dst.) untuk dimasukkan ke tabel transisi di jurnal.

2. **Data Hasil Load Testing (HTML Report)**:
   Buka berkas HTML hasil pengujian yang tersimpan di folder `result/` (`tanpa_hpa.html` dan `dengan_hpa.html`) menggunakan web browser. Laporan ini secara otomatis menampilkan:
   - **Average Response Time** (Waktu respons rata-rata)
   - **95th Percentile Response Time** (p95 response time)
   - **Failure Rate (%)** (Persentase request gagal)
   - **RPS (Requests Per Second)** / Throughput
   - Grafik performa time-series lengkap.

---

## Troubleshooting

### Port-forwarding gagal atau port 8080 bentrok
* **Gejala**: `Address already in use` atau program tidak bisa terhubung ke LMS.
* **Solusi**: Script `start.py` sudah otomatis mendeteksi dan menghentikan proses port-forward lama yang menggantung pada port 8080 sebelum memulai pengujian baru. Jika kendala berlanjut, pastikan tidak ada aplikasi lokal lain (seperti Apache/XAMPP) yang sedang menggunakan port 8080.

### Pod stuck di status `CrashLoopBackOff`
* **Solusi**: Deploy ulang manifest untuk menyegarkan resource:
  ```bash
  python sync.py
  ```
  Atau periksa log container sidecar untuk menganalisis error:
  ```bash
  kubectl logs -l app=moodle-app -c stress-sidecar --tail=50
  ```

### TARGETS pada HPA berstatus `<unknown>`
* **Gejala**: `kubectl get hpa` tidak menampilkan persentase penggunaan CPU.
* **Solusi**: Pastikan Metrics Server sudah aktif (`minikube addons enable metrics-server`). Metrics Server memerlukan waktu sekitar 1-2 menit setelah klaster menyala untuk mulai mengumpulkan statistik CPU.
