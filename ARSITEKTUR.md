# Arsitektur Sistem — Cerita dari Awal Sampai Akhir

Cerita ini menjelaskan gimana program `start.py` bekerja dari bangun tidur sampai tidur lagi. Bahasa santai, bukan untuk jurnal.

---

## Cerita: Dari Nol Sampai Dapet Data

### Step 1: Buka Docker Desktop

Kenapa? Karena **Minikube** (komputer mini) butuh tempat tinggal. Docker Desktop itu rumahnya. Kalau Docker belum nyala, program bilang "HEI, HIDUPKIN DOCKER DULU!" dan berhenti.

### Step 2: `python start.py`

Muncul menu:

```
1. Tanpa HPA      -> 1 pod doang, gak bisa nambah
2. Dengan HPA     -> pod bisa nambah otomatis kalo CPU berat
3. Interaktif     -> kamu atur sendiri mau berapa user
4. Perbandingan   -> coba 6 kombinasi skenario
5. Reset Klaster  -> hapus semua, balik ke awal
6. Keluar
```

### Step 3: Pilih 1 / 2 / 4, maka program jalanin:

#### Fase 1: Cek Prasyarat
Cek apakah ini ada:
- **Docker** — udah dibuka kan?
- **Minikube** — ini alat buat bikin "komputer mini" di laptop kamu
- **kubectl** — alat buat ngomong sama komputer mini
- **Python** — bahasa yang dipake program ini
- **Locust** — alat buat nyimakin ribuan user
- **Flask, PyYAML** — temen-temennya Python

Kalo ada yang kurang, program install otomatis.

#### Fase 2: Nyalain Komputer Mini (Minikube)
Program jalanin perintah:
```bash
minikube start --driver=docker --cpus=6 --memory=7GB
```

Ini kayak:
- Nyalain komputer mini di dalem Docker
- Kasih CPU 6 inti, RAM 7 GB
- Pake containerd (mesin buat jalanin container)
- Butuh 1-3 menit kalo pertama kali

Terus pasang **Metrics Server** — alat ukur "panasnya" CPU. Biar HPA bisa ngukur kapan harus nambah pod. Program juga seting biar Metrics Server ngukur setiap 15 detik (default 60 detik — terlalu lambat).

Terakhir, **preload image** — download `nginx:alpine` dan `python:3.11-alpine` ke cache Minikube. Biar pas pod baru lahir, gak perlu download lagi.

#### Fase 3: Deploy Aplikasi ke Komputer Mini

Program panggil `sync.py`:
1. Baca file `index.html`, `tugas.html`, `style.css`
2. Generate file `lms-setup.yaml` yang isinya **8 resource**:

```
1. ConfigMap: lms-html-index      -> file index.html
2. ConfigMap: lms-html-tugas      -> file tugas.html
3. ConfigMap: lms-css-style       -> file style.css
4. ConfigMap: lms-nginx-conf      -> config nginx
5. ConfigMap: lms-stress-script   -> kode Python si sidecar
6. Deployment: moodle-deployment  -> ngatur pod
7. Service: moodle-service        -> pintu masuk ke pod
8. Ingress: moodle-ingress        -> (tambahan untuk L7)
9. HPA: moodle-hpa                -> autoscaling
```

3. `kubectl apply -f lms-setup.yaml` — magic! Semua kebikin.
4. `kubectl rollout status` — tunggu sampe podnya beneran jalan.

Kalo milih **Tanpa HPA** (nomor 1): HPA dihapus, pod dipaksa jadi 1 doang.
Kalo milih **Dengan HPA** (nomor 2): HPA dibiarin aktif, pod bisa nambah sendiri.

### Yang ada di dalem pod?

```
+----------------------------------------------------------+
| POD                                                       |
|                                                           |
|  +----------------------------+  +---------------------+  |
|  | NGINX (port 80)            |  | SIDECAR (port 5000)  |  |
|  |                            |  |                      |  |
|  | /             -> index.htm |  | /api/courses ->      |  |
|  | /tugas.html   -> tugas.htm |  |   15.000 SHA-256     |  |
|  | /health       -> 200 OK    |  |   pake 4 CPU worker  |  |
|  | /api/courses  -> proxy ke  |  |   butuh ~3 detik     |  |
|  |                 sidecar    |  |                      |  |
|  +----------------------------+  +---------------------+  |
+----------------------------------------------------------+
```

**NGINX** itu server web. Kerjanya:
- Kalo ada yang minta `/` atau `/tugas.html` — langsung kasih file HTML (cepet, ~4ms)
- Kalo ada yang minta `/health` — bilang "I'm OK!" (paling cepet, ~2ms)
- Kalo ada yang minta `/api/courses` — suruh ke **sidecar** (si lambat)

**SIDECAR** itu Python. Kerjanya:
- Terima request `/api/courses`
- Hitung SHA-256 15.000 kali (biar CPU kepanasan)
- Pake 4 worker sekaligus
- Butuh ~3 detik per request — INI YANG BIKIN LAMBAT

Kenapa sengaja dibikin lambat? Biar HPA punya alasan buat nambah pod. Kalo semua endpoint cepet, HPA diem aja.

#### Fase 4: Nyambung ke LMS

Program coba 3 cara buat nyambung ke pod:

**Cara 1: minikube tunnel (paling kenceng)**
```
Locust -> http://192.168.49.2:80 -> Ingress -> Service -> Pod
```
- Jalanin `minikube tunnel` di background
- Butuh izin Admin (popup UAC)
- **Kalo berhasil**: data valid buat jurnal

**Cara 2: minikube service (lumayan)**
```
Locust -> http://192.168.49.2:30001 -> Service -> Pod
```
- `minikube service moodle-service --url`
- Dapet URL + port random
- Gak perlu admin

**Cara 3: kubectl port-forward (darurat)**
```
Locust -> http://localhost:8080 -> [kubectl port-forward] -> Service -> Pod
```
- `kubectl port-forward service/moodle-service 8080:80`
- **Cuman kuat ~150 goroutine** — kalo lebih, request numpuk, timeout, gagal
- Ini bottleneck utama!

Kalo semua gagal, program nyerah dan minta kamu cek manual.

#### Fase 5: Load Test (Locust)

**Kalo milih 1 atau 2 (headless):**
```bash
locust -f locustfile.py --headless -u 500 -r 50 -t 5m --host http://...
```

Artinya:
- `-u 500` — 500 orang dateng barengan
- `-r 50` — 50 orang per detik datengnya
- `-t 5m` — test jalan 5 menit
- `--headless` — gak pake tampilan web

Masing-masing orang (user) ngelakuin:
- 3x ngeliat dashboard (`/`)
- 3x ngeliat tugas (`/tugas.html`)
- 5x liat nilai (`/api/courses`) — ini paling sering
- 1x cek kesehatan (`/health`)

**Kalo milih 3 (interaktif):**
Kebuka browser:
- `http://localhost:8089` — Locust Web UI (atur user di sini)
- `http://{host}` — LMS-nya langsung

**Kalo milih 4 (perbandingan):**
Jalanin 6 kali test: 2 skenario x 3 mode:
- Tanpa HPA + Direct Pod, L4 Service, L7 Ingress
- Dengan HPA + Direct Pod, L4 Service, L7 Ingress

Masing-masing 150 user, 3 menit. Abis itu dapet laporan `result/perbandingan.html`.

### Abis test selesai:
1. Matikan port-forward / tunnel
2. Simpen hasil CSV ke `result/csv/`
3. Balik ke menu utama
4. Bisa milih 4 (perbandingan) buat liat grafik

---

## 3 Mode Load Balancing (Cerita)

### Mode 1: Direct Pod — "Langsung ketuk pintu"
```
Kamu -> kubectl port-forward -> pod si A
```
- Kayak kamu tau persis rumah temen kamu, langsung dateng ke rumahnya doang
- Gak ada resepsionis, gak ada antar jemput
- Kalo di rumah itu lagi sibuk, ya antri

### Mode 2: L4 Service — "Resepsionis gedung"
```
Kamu -> kubectl port-forward -> Service -> pod (bulak-balik)
```
- Ada resepsionis di gedung
- Kamu bilang "saya mau ke siapa" → resepsionis nentuin ke rumah mana
- Enaknya: beban dibagi rata ke semua rumah
- Tapi: resepsionisnya masih lewat kubectl port-forward yang lemah

### Mode 3: L7 Ingress — "Resepsionis + satpam canggih"
```
Kamu -> http://192.168.49.2 -> Ingress Controller -> Service -> pod
```
- Ini yang paling canggih
- Ada satpam (Ingress) yang bisa baca "oh ini maunya / (halaman depan)" atau "/api/courses"
- Gak perlu kubectl port-forward sama sekali
- Satpam ini tinggal di dalem komplek, bukan di luar pagar

---

## Kenapa Pake Port-Forward Data Gak Valid?

Bayangin gini:

| Pake port-forward | Pake tunnel |
|---|---|
| Gerbang masuk cuma 1 jalur | Gerbang masuk 8 jalur (kayak tol) |
| Mobil (request) antri panjang | Mobil jalan lancar |
| Yang lewat cuma ~150 mobil/detik | Bisa ribuan mobil/detik |
| HPA nambah rumah, tapi jalan masuk tetap 1 | HPA nambah rumah + jalan masuk lega |
| HPA keliatannya gak berguna | HPA keliatan ngebantu banget |

**Angka pastinya:**
- 500 user, port-forward -> 52 request/detik
- 500 user, tunnel -> 5000+ request/detik (static endpoints)

---

## Siapa Ngapain (Role Play)

```
[HPA] — Bos yang ngatur jumlah pekerja
   Ngitung: "CPU udah 40% nih, butuh 2 orang lagi"
   "CPU masih 10%, 1 orang cukup"

[Service] — Resepsionis
   Tugas: ngebagi tamu ke pekerja yang free
   Caranya: round-robin (gantian)

[Ingress] — Satpam canggih
   Bisa bedain: "oh ini minta /, arahkan ke dapur"
                "oh ini /api/courses, arahkan ke bagian IT"

[nginx] — Staff front desk
   Kalo diminta / atau /tugas.html: langsung kasih (cepet)
   Kalo diminta /api/courses: "eh ini urusan IT, tolong ke samping"
   
[sidecar] — Staff IT
   Kerjaan: ngitung hash SHA-256 15.000 kali
   Lama: ~3 detik per orang

[Locust] — Rombongan 500 pengunjung
   Dateng rame-rame, macem-macem maunya

[kubectl port-forward] — Pintu darurat yang sempit
      :( cuma muat ~150 orang lewat
```

---

## Diagram untuk Jurnal (Versi Simpel)

Kalo mau gambar diagram buat jurnal, gambarnya segini aja:

```
+------------------+     +--------------------+     +------------------+
|  LOAD GENERATOR  | --> |  KONEKSI / TUNNEL  | --> |  KUBERNETES      |
|  (Locust)        |     |  (tunnel / PF /    |     |  CLUSTER         |
|  500 user        |     |   minikube service)|     |  (Minikube)      |
|  4 endpoint      |     |                    |     |                  |
+------------------+     +--------------------+     +------------------+
                                                           |
                                               +-----------+-----------+
                                               |                       |
                                        +-----+-----+          +------+------+
                                        |   NGINX    |          |   SIDECAR   |
                                        |  (port 80) |          |  (port 5000) |
                                        |            |          |             |
                                        | / -> html  |          | SHA-256     |
                                        | /health->OK|          | x 15.000    |
                                        | /api/* ->  |--------->| 4 worker    |
                                        |   sidecar  |          |             |
                                        +------------+          +-------------+
                                               |
                                        [ConfigMaps]
                                        - index.html
                                        - tugas.html
                                        - style.css
```

---

## Boleh dicoba sekarang

```bash
# 1. Buka Docker Desktop

# 2. Buka PowerShell Admin buat tunnel
minikube tunnel

# 3. Buka PowerShell biasa
python start.py

# 4. Pilih 4 (Perbandingan)
# 5. Tunggu ~30 menit
# 6. Buka result/perbandingan.html
```

Kalo males baca, intinya:
- **Buka Docker** → **`python start.py`** → **pilih menu** → **dapet hasil**
- Port-forward lemah, tunnel kenceng
- HPA ngebantu kalo pake tunnel
- Sidecar sengaja dibikin lambat biar HPA kepancing
