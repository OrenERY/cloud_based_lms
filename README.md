# LMS UNSAP – Kubernetes HPA & Load Testing Prototype

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Repository ini berisi prototipe arsitektur klaster Kubernetes untuk menguji elastisitas infrastruktur **LMS UNSAP** menggunakan **Horizontal Pod Autoscaler (HPA)**. Pengujian performa disimulasikan menggunakan **Locust** dengan **500 concurrent users** di bawah beban pengujian lokal.

---

## Spesifikasi Device Penelitian

| Komponen   | Spesifikasi                         |
|------------|-------------------------------------|
| **OS**     | Windows 11, WSL2 aktif              |
| **CPU**    | Intel Core i5-12450H (12th Gen)     |
| **RAM**    | 16 GB DDR4                          |
| **GPU**    | NVIDIA RTX 3050 Laptop              |
| **Tools**  | Docker Desktop, Minikube, kubectl, Python/Locust |
| **Shell**  | Semua perintah dijalankan dari **WSL2** |

---

## Komponen & Prasyarat Sistem

1. **Docker Desktop** (WSL 2 backend pada Windows 11).
2. **Minikube** (v1.x) sebagai orchestrator klaster Kubernetes lokal.
3. **Kubectl** untuk manajemen resource klaster.
4. **Python 3.x** (ditambah **Locust** untuk menjalankan skenario *load testing*).

---

## Struktur Berkas

| File | Deskripsi |
|------|-----------|
| `index.html` | Halaman utama Dasbor LMS UNSAP (lokal) |
| `tugas.html` | Halaman pengumpulan tugas LMS UNSAP (lokal) |
| `style.css` | Stylesheet utama tampilan LMS (lokal) |
| `sync.py` | Script Python otomatis untuk menyinkronkan file HTML/CSS lokal ke dalam manifes Kubernetes dan mendeploy ulang ke cluster. Termasuk konfigurasi nginx tuning, CPU stress sidecar, dan HPA |
| `lms-setup.yaml` | *Auto-generated* — Manifest Kubernetes (hasil generate otomatis dari `sync.py`) yang berisi ConfigMaps, Deployment Nginx + Python sidecar, Service, dan HPA |
| `locustfile.py` | Script pengujian Locust untuk mensimulasikan trafik 500 mahasiswa concurrent |
| `requirements.txt` | Dependensi Python (Locust) |
| `result/` | Folder berisi hasil report HTML dari Locust |


---

## Panduan Memulai (Setup & Deployment di WSL2)

> **PENTING**: Jalankan **SEMUA perintah** (kubectl, locust, python) dari dalam **WSL2 terminal**, **BUKAN** dari PowerShell atau CMD Windows.

### Langkah 0a: Clone & Install Dependencies

```bash
git clone https://github.com/<username>/jurnal-LMS.git
cd jurnal-LMS
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Langkah 0: Konfigurasi Docker Desktop (Windows)

Buka **Docker Desktop → Settings → Resources** dan atur:
- **CPUs**: `4` (minimum)
- **Memory**: `6 GB` (minimum)
- Pastikan **WSL 2 based engine** sudah dicentang pada **Settings → General**

Klik **Apply & Restart**.

> **Verifikasi**: Pastikan Docker Desktop sudah running (ikon Docker di system tray berwarna hijau/stabil).

---

### Langkah 1: Jalankan Klaster Minikube (dari WSL2)

1. Buka terminal WSL2, lalu jalankan Minikube dengan resource terbatas:
   ```bash
   minikube start --driver=docker --cpus=3 --memory=4096
   ```
   > **Constraint**: Minikube dibatasi maksimal **3 CPU** dan **4 GB RAM** sesuai kapasitas device.

2. Aktifkan **Metrics Server** agar HPA dapat memantau utilitas CPU Pod secara real-time:
   ```bash
   minikube addons enable metrics-server
   ```

3. **Verifikasi Metrics Server** — Pastikan status `AVAILABLE` menunjukkan `True`:
   ```bash
   kubectl get apiservice v1beta1.metrics.k8s.io
   ```
   Contoh output yang benar:
   ```
   NAME                         SERVICE                      AVAILABLE   AGE
   v1beta1.metrics.k8s.io       kube-system/metrics-server   True        2m
   ```
   > Jika masih `False`, tunggu 1-2 menit lalu cek ulang.

---

### Langkah 2: Sinkronisasikan & Deploy File ke Kubernetes

Setiap kali Anda mengedit `index.html`, `tugas.html`, atau `style.css`, deploy otomatis ke Kubernetes:
```bash
python3 sync.py
```
*Script ini merakit ConfigMap (HTML/CSS/nginx/stress-script), menerapkan manifest `lms-setup.yaml`, dan merestart pod Nginx + sidecar.*

**Verifikasi Deployment** — Pastikan semua pod berjalan dengan **2/2 READY** (nginx + sidecar):
```bash
kubectl get pods
```
Contoh output yang benar:
```
NAME                                  READY   STATUS    RESTARTS   AGE
moodle-deployment-xxxxxxxxx-xxxxx     2/2     Running   0          30s
```

> **PENTING**: Kolom READY harus menunjukkan **2/2**, bukan 1/2 or 0/2.  
> Jika masih belum ready, tunggu dan pantau dengan:
> ```bash
> kubectl get pods -w
> ```
> Jika pod stuck di `CrashLoopBackOff` atau `Error`, periksa log:
> ```bash
> kubectl logs <nama-pod> -c moodle        # Log container nginx
> kubectl logs <nama-pod> -c stress-sidecar # Log container Python sidecar
> ```

---

### Langkah 3: Port Forward dari WSL2

Jalankan perintah ini di **jendela terminal WSL2 terpisah** (jangan ditutup selama pengujian):
```bash
kubectl port-forward svc/moodle-service 8080:80
```

**Verifikasi Koneksi** — Buka terminal WSL2 **lain** dan jalankan:
```bash
curl http://localhost:8080/health
```
Harus mengembalikan:
```
OK
```

Uji juga endpoint lainnya:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/          # Harus 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/tugas.html # Harus 200
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/courses # Harus 200 (lambat ~2-5 detik karena CPU stress)
```

> **Jika `curl` gagal dengan `Connection refused`**: Berarti port-forward mati atau pod belum ready. Kembali ke Langkah 2 dan verifikasi ulang.

---

## Skenario Pengujian Beban (Load Testing)

### Parameter Pengujian

| Parameter         | Nilai                                               |
|-------------------|-----------------------------------------------------|
| **Total Users**   | 500 concurrent users                                |
| **Spawn Rate**    | 10-20 users/second                                  |
| **Target Host**   | `http://localhost:8080`                              |
| **Locust Mode**   | Distributed (1 master + 2 workers)                  |
| **wait_time**     | `between(0.5, 1.5)` detik                           |
| **Task Weight**   | `/api/courses` = 8 (CPU stress), dashboard = 3, tugas = 2, health = 1 |

### Menjalankan Locust Distributed (dari WSL2)

Untuk menangani 500 concurrent users secara optimal, jalankan Locust dalam mode **distributed** dengan 1 master dan 2 worker:

**Terminal 1 – Master:**
```bash
locust -f locustfile.py --master
```

**Terminal 2 – Worker 1:**
```bash
locust -f locustfile.py --worker --master-host=127.0.0.1
```

**Terminal 3 – Worker 2:**
```bash
locust -f locustfile.py --worker --master-host=127.0.0.1
```

Buka interface Locust di [http://localhost:8089](http://localhost:8089).
Masukkan **Number of users**: `500`, **Spawn rate**: `10`, dan **Host**: `http://localhost:8080`. Klik **Start swarming**.

### Alternatif: Locust Headless (CLI Langsung)

Jika tidak ingin menggunakan web UI, jalankan langsung dari terminal:
```bash
locust -f locustfile.py --host=http://localhost:8080 \
  --users=500 --spawn-rate=10 --run-time=2m30s \
  --html=result/nama_test.html --headless
```

---

## Skenario A: Sistem Eksisting (Tanpa HPA – Statis 1 Pod)

Mensimulasikan kegagalan sistem akibat kelebihan beban ketika server dikunci hanya pada 1 Pod statis.

### A1. Persiapan — Matikan HPA

```bash
# Hapus aturan HPA
kubectl delete hpa moodle-hpa

# Kunci replika pada 1 Pod
kubectl scale deployment moodle-deployment --replicas=1
```

### A2. Verifikasi Sebelum Test

> **WAJIB dilakukan sebelum menjalankan Locust!** Jika dilewati, hasil test bisa invalid (100% failure).

```bash
# 1. Cek pod berjalan (harus READY 2/2, STATUS Running)
kubectl get pods

# 2. Cek HPA sudah terhapus
kubectl get hpa
# Harus menampilkan: No resources found in default namespace.

# 3. Cek replika hanya 1
kubectl get deployment moodle-deployment
# READY harus 1/1

# 4. Pastikan port-forward masih aktif (di terminal terpisah)
kubectl port-forward svc/moodle-service 8080:80

# 5. Test koneksi manual
curl http://localhost:8080/health
# Harus return: OK
```

### A3. Jalankan Locust

```bash
# Via Web UI (distributed)
# Terminal 1: locust -f locustfile.py --master
# Terminal 2: locust -f locustfile.py --worker --master-host=127.0.0.1
# Terminal 3: locust -f locustfile.py --worker --master-host=127.0.0.1
# Buka http://localhost:8089, isi 500 users, spawn rate 10, host http://localhost:8080

# ATAU via CLI (headless)
locust -f locustfile.py --host=http://localhost:8080 \
  --users=500 --spawn-rate=10 --run-time=2m30s \
  --html=result/tanpa_hpa.html --headless
```

### A4. Hasil yang Diharapkan
- Response time `/api/courses` membengkak (>2000ms, bisa sampai 60-90 detik)
- Failure rate `/api/courses` meningkat signifikan (502/504 errors)
- Endpoint statis (`/`, `/tugas.html`, `/health`) tetap responsif (~3-20ms)
- CPU container mengalami throttling berat

---

## Skenario B: Sistem Usulan (HPA Aktif)

Menguji keandalan mekanisme autoscaling dalam mendistribusikan beban kerja secara otomatis.

### B1. Persiapan — Aktifkan HPA Kembali

```bash
# Aktifkan kembali HPA & Deploy ulang
python3 sync.py
```

### B2. Verifikasi Sebelum Test

> **WAJIB dilakukan sebelum menjalankan Locust!**

```bash
# 1. Cek pod berjalan (harus READY 2/2, STATUS Running)
kubectl get pods

# 2. Cek HPA sudah aktif
kubectl get hpa
# Harus menampilkan moodle-hpa dengan TARGETS dan MINPODS/MAXPODS

# 3. Pastikan port-forward masih aktif (di terminal terpisah)
kubectl port-forward svc/moodle-service 8080:80

# 4. Test koneksi manual
curl http://localhost:8080/health
# Harus return: OK

# 5. Test endpoint /api/courses (akan lambat ~2-5 detik, itu normal)
curl http://localhost:8080/api/courses
# Harus return JSON: {"status": "ok", "hash_rounds": 80000, "courses": [...]}
```

### B3. Pantau HPA (Terminal Terpisah)

Buka terminal WSL2 tambahan untuk memantau autoscaling secara real-time:
```bash
# Pantau perubahan HPA
kubectl get hpa moodle-hpa -w

# ATAU pantau penambahan pod
kubectl get pods -l app=moodle-app -w
```

### B4. Jalankan Locust

```bash
# Via Web UI (distributed) — sama seperti Skenario A
# ATAU via CLI (headless)
locust -f locustfile.py --host=http://localhost:8080 \
  --users=500 --spawn-rate=10 --run-time=2m30s \
  --html=result/dengan_hpa.html --headless
```

### B5. Hasil yang Diharapkan
- HPA melakukan scale-up (1 → 2 → 3 → hingga 5 Pod) saat CPU > 50%
- Response time `/api/courses` menurun setelah scale-up
- Failure rate lebih rendah dibanding Skenario A
- Endpoint statis tetap responsif

### Konfigurasi HPA

| Parameter            | Nilai |
|----------------------|-------|
| **Min Replicas**     | 1     |
| **Max Replicas**     | 5     |
| **CPU Threshold**    | 50%   |

---

## Arsitektur Pod

Setiap Pod terdiri dari 2 container:

| Container          | Image              | Fungsi                                    | CPU Request | CPU Limit |
|--------------------|--------------------|-------------------------------------------|-------------|-----------|
| **moodle** (main)  | `nginx:alpine`     | Serving HTML/CSS + proxy ke stress endpoint | 150m        | 400m      |
| **stress-sidecar** | `python:3.11-alpine` | CPU stress server (hashing loop 80k rounds) | 50m         | 200m      |

Endpoint `/api/courses` di nginx mem-proxy request ke sidecar Python (port 5000) yang melakukan **80.000 iterasi SHA-256 hashing** per request, menghasilkan beban CPU nyata untuk memicu HPA.

---

## Troubleshooting

### Semua request Locust gagal (100% failure rate)

**Gejala**: Semua endpoint mengembalikan `ConnectionRefusedError` atau `ConnectionError`.

**Penyebab**: Service Kubernetes tidak bisa diakses — port-forward mati atau pod tidak running.

**Solusi**:
```bash
# 1. Cek apakah pod masih running
kubectl get pods
# Jika tidak ada pod atau STATUS bukan Running, deploy ulang:
python3 sync.py

# 2. Restart port-forward
kubectl port-forward svc/moodle-service 8080:80

# 3. Verifikasi koneksi
curl http://localhost:8080/health
```

### Pod stuck di CrashLoopBackOff

**Solusi**:
```bash
# Cek log error
kubectl logs <nama-pod> -c stress-sidecar
kubectl describe pod <nama-pod>

# Biasanya cukup deploy ulang
python3 sync.py
```

### HPA tidak melakukan scale-up

**Gejala**: `kubectl get hpa` menunjukkan `<unknown>` pada kolom TARGETS.

**Solusi**:
```bash
# Pastikan metrics-server aktif
minikube addons enable metrics-server

# Tunggu 1-2 menit, lalu cek lagi
kubectl get hpa
# TARGETS harus menunjukkan angka (misal: 12%/50%)
```

### Port-forward terputus setelah test selesai

Port-forward bisa mati jika pod di-restart oleh Kubernetes. Sebelum menjalankan test berikutnya:
```bash
# Kill port-forward yang lama (jika ada)
# Lalu jalankan ulang:
kubectl port-forward svc/moodle-service 8080:80
```

### `/api/courses` mengembalikan 502/504 tapi endpoint lain normal

Ini **bukan error konfigurasi** — ini adalah perilaku yang diharapkan pada Skenario A (tanpa HPA). Python sidecar kewalahan memproses 80k hash rounds saat banyak concurrent request.

---

## Checklist Cepat Sebelum Setiap Test

Gunakan checklist ini **setiap kali** sebelum menjalankan Locust:

- [ ] Docker Desktop berjalan
- [ ] Minikube running (`minikube status` → `Running`)
- [ ] Pod ready 2/2 (`kubectl get pods` → `2/2 Running`)
- [ ] Port-forward aktif (`kubectl port-forward svc/moodle-service 8080:80`)
- [ ] Health check berhasil (`curl http://localhost:8080/health` → `OK`)
- [ ] HPA sesuai skenario (`kubectl get hpa` → ada/tidak ada sesuai kebutuhan)

---

## Cara Mengambil Data untuk Laporan Jurnal

1. **Log Transisi Autoscaling (Terminal Output)**:
   Catat waktu dan perubahan replika saat HPA melakukan scale-up (misal dari `1` ke `3` hingga `5` pod) dari pemantauan `kubectl get hpa -w`.
2. **Grafik Performa (Locust Charts)**:
   Unduh grafik **Total Requests per Second (RPS)** dan **Response Times (ms)** langsung dari tab **Charts** di Locust (pilih menu tiga garis di pojok kanan atas grafik → *Download PNG*).
3. **Data Mentah Statistik (CSV)**:
   Unduh file statistik di tab **Download Data** di Locust (*Download request statistics CSV* dan *Download test history CSV*) untuk pembuatan grafik kustom di Excel/Word.
4. **Data via Locust HTML Report**:
   Jika menggunakan mode headless (`--html=result/nama.html`), buka file HTML hasil test di browser. Semua statistik, grafik, dan tabel sudah tersedia di dalamnya.
5. **Metrik Ringkasan**:
   Catat nilai **Average Response Time**, **95th Percentile Response Time**, dan **Failure Rate (%)** untuk dimasukkan ke tabel komparasi hasil pengujian jurnal Anda.
