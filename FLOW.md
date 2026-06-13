# Flow Program — jurnal-LMS

```mermaid
flowchart TB
    START([python start.py]) --> F1[FASE 1: Cek Prasyarat]
    F1 --> |Docker, Minikube, kubectl, Locust| MENU

    subgraph MENU [Menu Interaktif]
        direction LR
        M1[1: Tanpa HPA] --> PIPELINE
        M2[2: Dengan HPA] --> PIPELINE
        M3[3: Interaktif UI] --> PIPELINE
        M4[4: Perbandingan LB] --> COMPARE
        M5[5: Reset Klaster]
        M6[6: Keluar]
    end

    subgraph PIPELINE [Flow Skenario A / B / 3]
        F2[FASE 2: Setup Klaster]
        F3[FASE 3: Deploy Manifest]
        F4[FASE 4: Setup Koneksi]
        F5[FASE 5: Load Test]

        F2 --> |ensure_docker| DOCKER{docker info}
        DOCKER --> |OK| MINIKUBE{minikube status}
        MINIKUBE --> |running| METRICS
        MINIKUBE --> |stop| MINIKUBE_START[minikube start<br/>6 CPU, 7GB RAM]
        MINIKUBE_START --> METRICS[ensure_metrics_server]
        METRICS --> |enable & patch 15s| IMAGES[preload_images<br/>nginx:alpine + python:3.11-alpine]
        IMAGES --> NODE[verify_node_resources]

        NODE --> F3
        F3 --> |python sync.py| YAML[generate lms-setup.yaml<br/>dari index.html, tugas.html, style.css]
        YAML --> |kubectl apply| DEPLOY[Deploy ke cluster]
        DEPLOY --> |kubectl rollout status| WAIT[tunggu pod Running]
        WAIT --> |Skenario A| NO_HPA[kubectl delete hpa<br/>kubectl scale --replicas=1]
        WAIT --> |Skenario B| YES_HPA[Re-apply manifest dgn HPA]

        NO_HPA --> F4
        YES_HPA --> F4

        F4 --> TUNNEL{Coba tunnel dulu}
        TUNNEL --> |minikube tunnel OK| TUN_OK[Pakai External IP]
        TUNNEL --> |gagal| SVC_URL{coba minikube service}
        SVC_URL --> |OK| SVC_OK[Pakai Service URL]
        SVC_URL --> |gagal| PF_FALLBACK[kubectl port-forward]
        PF_FALLBACK --> PF_OK[Pakai localhost:8080<br/>kapasitas ~150 goroutine]

        TUN_OK --> F5
        SVC_OK --> F5
        PF_OK --> F5

        F5 --> |1/2/3: Tanpa HPA| LOCUST_A[locust headless<br/>default 500 users / 5m]
        F5 --> |2: Dengan HPA| LOCUST_B[locust headless<br/>default 500 users / 5m]
        F5 --> |3: Interaktif| LOCUST_UI[buka Locust Web UI + LMS]
        LOCUST_A --> DONE([Selesai])
        LOCUST_B --> DONE
    end

    subgraph COMPARE [Flow Skenario C: Perbandingan]
        C_F2[FASE 2: Setup Klaster<br/>Docker > Minikube > Metrics Server]
        C_F2 --> INGRESS[ensure_ingress]
        INGRESS --> |minikube addons list| IC_CEK{Cek status}
        IC_CEK --> |sudah enabled| IC_WAIT[Tunggu ingress-nginx<br/>kubectl wait --for=condition=ready<br/>--timeout=120s]
        IC_CEK --> |belum| IC_ENABLE[minikube addons enable ingress<br/>timeout 60s]
        IC_ENABLE --> IC_WAIT
        IC_WAIT --> CHECK_PF{Deteksi<br/>koneksi}
        CHECK_PF --> |port-forward| WARN[Peringatan user > 150<br/>Input konfirmasi y/n]
        CHECK_PF --> |tunnel| CT_UNLOCK
        WARN --> |cancel| ABORT([Dibatalkan user])
        WARN --> |lanjut| CT_UNLOCK

        CT_UNLOCK --> LOOP_SC[Loop:<br/>tanpa_hpa -> dengan_hpa]

        subgraph PER_SCENARIO [Per Skenario x Per Mode]
            SC_INNER[deploy_for_scenario]
            SC_INNER --> |python sync.py<br/>kubectl apply| YAML_GEN[generate lms-setup.yaml<br/>include 8 resources:<br/>ConfigMap, Secret, Deployment,<br/>Service, Ingress moodle-ingress, HPA]

            YAML_GEN --> |Skenario tanpa_hpa| SC_A[kubectl delete hpa moodle-hpa<br/>kubectl scale --replicas=1]
            YAML_GEN --> |Skenario dengan_hpa| SC_B[kubectl apply -f lms-setup.yaml<br/>HPA tetap aktif]
            SC_A --> ROLLOUT[kubectl rollout status<br/>--timeout=120s<br/>sleep 5]
            SC_B --> ROLLOUT

            ROLLOUT --> MONITOR_CEK{Skenario<br/>dengan_hpa?}
            MONITOR_CEK --> |ya| HPA_LOG[monitor_hpa<br/>kubectl get hpa moodle-hpa<br/>catat ke file log]
            MONITOR_CEK --> |tidak| SETUP_MODE
            HPA_LOG --> SETUP_MODE

            SETUP_MODE{3 Mode LB} --> |Direct Pod| POD_DIRECT[setup_pod_forward]
            SETUP_MODE --> |L4 Service| L4_SVC[setup_service_l4]
            SETUP_MODE --> |L7 Ingress| L7_ING[setup_ingress_connection]

            subgraph POD_DIRECT [Tanpa LB]
                P1[get_first_pod_name<br/>kubectl get pods -l app=moodle-app]
                P2[free_port 8081]
                P3[kubectl port-forward pod/{name} 8081:80<br/>stdout/stderr ke DEVNULL]
                P4[wait 5 detik]
                P1 --> P2 --> P3 --> P4 --> HOST_POD[host = http://localhost:8081<br/>proc = Popen object]
            end

            subgraph L4_SVC [L4 Service LB]
                S1[start_port_forward]
                S2[kubectl port-forward<br/>service/moodle-service 8080:80<br/>stdout/stderr ke DEVNULL]
                S3[wait 3 detik]
                S1 --> S2 --> S3 --> HOST_SVC[host = http://localhost:8080<br/>proc = Popen object]
            end

            subgraph L7_ING [L7 Nginx Ingress LB]
                I1[minikube ip<br/>timeout 10s]
                I2{IP valid?}
                I1 --> I2
                I2 --> |tidak| I_FAIL[return None, None]
                I2 --> |ya| I3[IP = stdout.strip]
                I3 --> I4[sleep 8 detik<br/>tunggu ingress route stabil]
                I4 --> HOST_ING[host = http://{ip}<br/>proc = None<br/>tidak perlu port-forward!]
            end

            POD_DIRECT --> VFY[verify_connection<br/>health check 5x retry]
            L4_SVC --> VFY
            L7_ING --> VFY
            HOST_POD --> VFY
            HOST_SVC --> VFY
            HOST_ING --> VFY

            VFY --> |gagal| SKIP[Skip mode ini]
            VFY --> |ok| LOG_HPA[log_hpa_scaling<br/>catat ke file]
            LOG_HPA --> LOADTEST[run_locust_headless]
            LOADTEST --> |locust command| LOCUST_CMD[locust -f locustfile.py<br/>--host={host}<br/>--users={users}<br/>--spawn-rate={spawn_rate}<br/>--run-time={run_time}<br/>--html={path}.html<br/>--csv={path}.csv<br/>--headless]
            LOCUST_CMD --> SAVE[Simpan CSV ke<br/>result/csv/{mode}_{sc}.csv]
            SAVE --> CLEANUP2{Cleanup}
            CLEANUP2 --> |Direct Pod| KILL_PF[proc.terminate<br/>free_port 8081]
            CLEANUP2 --> |L4 Service| KILL_SVC[proc.terminate<br/>free_port 8080]
            CLEANUP2 --> |L7 Ingress| NO_CLEAN[Tidak ada cleanup<br/>tidak ada port-forward]
        end

        LOOP_SC --> PER_SCENARIO
        PER_SCENARIO --> LOOP_SC

        CLEANUP2 --> REPORT([Generate laporan])
        REPORT --> |parse semua CSV| GEN_HTML[generate_comparison_report<br/>5 Chart.js grafik<br/>Time-series chart<br/>Validity badges<br/>Bottleneck insight]
        REPORT --> GEN_COST[generate_cost_analysis<br/>5 tahun: OnPrem vs GCP vs GCP+HPA]
        GEN_HTML --> RESULT([result/perbandingan.html])
        GEN_COST --> COST_RESULT([result/analisis_biaya.html])
    end
```

---

## Penjelasan per fase

### FASE 1 — Cek Prasyarat (`check_prerequisites()`:744)
| Pengecekan | Deskripsi |
|---|---|
| Docker | `docker info` — pastikan Docker Desktop berjalan |
| Minikube | Cek binary di PATH, auto-install jika Windows |
| kubectl | Pastikan binary tersedia |
| Python | Minimal 3.8 |
| Locust | pip install jika belum ada |
| Deps Flask, PyYAML | pip install otomatis dari requirements |

### FASE 2 — Setup Klaster (`setup_cluster()`:425)
| Langkah | Kode | Detail |
|---|---|---|
| Docker | `ensure_docker()`:284 | `docker info`, timeout 10 detik |
| Minikube | `ensure_minikube()`:297 | `minikube start` dengan 6 CPU, 7 GB RAM, 50 GB disk, containerd |
| Metrics Server | `ensure_metrics_server()`:331 | `minikube addons enable metrics-server` lalu patch ke `--metric-resolution=15s`, tunggu API siap max 60 detik |
| Preload Image | `preload_images()`:392 | `minikube image load python:3.11-alpine nginx:alpine` |
| Verifikasi | `verify_node_resources()`:413 | `kubectl get node minikube` tampilkan CPU & RAM |

### FASE 3 — Deploy Manifest (`deploy_and_prepare()`:447 / `deploy_for_scenario()`:747)
1. `python sync.py` — membaca 3 file statis: `index.html`, `tugas.html`, `style.css`
2. Generate `lms-setup.yaml` yang berisi **8 resource**:
   - Namespace, ConfigMap (moodle-html), ConfigMap (stress.py), Secret (dummy)
   - Deployment (2 container: nginx + sidecar), Service moodle-service (ClusterIP)
   - **Ingress moodle-ingress** (`networking.k8s.io/v1`, `ingressClassName: nginx`)
   - HorizontalPodAutoscaler (CPU 30%, min 1, max 10)
3. `kubectl apply -f lms-setup.yaml`
4. `kubectl rollout status deployment/moodle-deployment --timeout=120s`
5. Sesuai skenario:
   - **Tanpa HPA**: `kubectl delete hpa moodle-hpa` (abaikan error jika tak ada) + `kubectl scale deployment/moodle-deployment --replicas=1`
   - **Dengan HPA**: `kubectl apply -f lms-setup.yaml` (HPA sudah ada di YAML)
6. Di Skenario C, Ingress resource sudah termasuk dan langsung di-apply tanpa langkah terpisah

### FASE 4 — Setup Koneksi

#### Skenario A / B / 3 — `setup_connection()`:595
Prioritas koneksi secara berurutan:

1. **minikube tunnel** (`try_tunnel()`:535)
   - `minikube tunnel --cleanup` (hapus rute lama)
   - Cek `kubectl get svc moodle-service -o jsonpath={.status.loadBalancer.ingress[0].ip}`
   - Jika sudah ada IP: tunnel aktif di terminal lain → pakai IP tersebut
   - Jika belum: `subprocess.Popen(["minikube", "tunnel"])` non-blocking
   - Verifikasi health check ke `http://IP`
   - Return `(tunnel_proc, ip)` jika sukses

2. **minikube service** (`try_minikube_service()`:565)
   - `minikube service moodle-service --url`
   - Parse URL dari output
   - Verifikasi health check

3. **kubectl port-forward** (`start_port_forward()`:578)
   - `kubectl port-forward service/moodle-service 8080:80` (Popen non-blocking)
   - Target: `http://localhost:8080`
   - Verifikasi health check 3x retry
   - **Keterbatasan**: port-forward kapasitas ~150 goroutine, tidak valid untuk HPA

#### Skenario C (Perbandingan) — 3 mode independen

| Mode | Fungsi | Metode | Port | Cleanup |
|---|---|---|---|---|
| **Direct Pod** (tanpa LB) | `setup_pod_forward()`:143 | `kubectl port-forward pod/{nama} 8081:80` | 8081 | `proc.terminate()` + `free_port(8081)` |
| **L4 Service** | `start_port_forward()`:578 | `kubectl port-forward service/moodle-service 8080:80` | 8080 | `proc.terminate()` + `free_port(8080)` |
| **L7 Ingress** | `setup_ingress_connection()`:160 | `minikube ip` → `http://{ip}` | **tidak ada** | **tidak perlu** — langsung ke Nginx Ingress controller |

### FASE 5 — Load Test (`run_test()` / `run_comparison()`)
#### Skenario A / B — Headless (`run_test()`)
- `locust -f locustfile.py --headless -u 500 -r 50 -t 5m --host <target>`
- Locust menyimpan CSV ke `result/csv/` dengan timestamp
- Laporan: `result/laporan.html` (rendered dengan LocustHTML)

#### Skenario 3 — Interaktif
- Buka browser ke `http://localhost:8089` (Locust Web UI) + `http://<target>` (LMS)
- User menentukan sendiri parameter test

#### Skenario C — Perbandingan (`run_comparison()`:825)
- **Fase 2+** — Setup klaster + enable Ingress controller via `ensure_ingress()`:108
  - Cek `minikube addons list` apakah ingress sudah enabled
  - Jika belum: `minikube addons enable ingress`, timeout 60s
  - `kubectl wait --namespace=ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller --timeout=120s`
- **Deteksi koneksi** — `check_is_port_forward()`:777
  - Cek `kubectl get svc moodle-service -o jsonpath={.status.loadBalancer.ingress[0].ip}`
  - Jika IP kosong → anggap port-forward → warning jika user > 150
  - Jika ada IP → tunnel aktif → throughput penuh
- **Loop** — 2 skenario (tanpa_hpa, dengan_hpa) x 2 atau 3 mode LB:
  - `deploy_for_scenario()`:747 — menjalankan `sync.py` yang generate `lms-setup.yaml` (termasuk resource Ingress `moodle-ingress`)
  - 3 Mode koneksi:
    1. **Direct Pod** (`setup_pod_forward()`:143): `kubectl port-forward pod/{nama} 8081:80`
    2. **L4 Service** (`start_port_forward()`:578): `kubectl port-forward service/moodle-service 8080:80`
    3. **L7 Ingress** (`setup_ingress_connection()`:160): `minikube ip` → `http://{ip}`, **tanpa port-forward** (Nginx Ingress controller sudah listen di port 80 Minikube IP)
  - Verify koneksi dengan 5x retry
  - Jika skenario dengan_hpa: `monitor_hpa()` + `log_hpa_scaling()` — catat replika
  - Locust: `--headless -u {users} -r {spawn_rate} -t {run_time} --csv result/csv/{mode}_{sc}`
  - Cleanup: terminate port-forward + free_port untuk mode Direct Pod & L4 Service; **L7 Ingress tidak perlu cleanup**
- **Generate report:**
  - `generate_comparison_report()` — parse CSV dari seluruh kombinasi
  - 5 Chart.js grafik: failure rate, response time, throughput, concurrency, time-series (99 titik)
  - Tabel perbandingan dengan validity badge
  - Jika Ingress gagal diaktifkan, tabel menampilkan "Data tidak tersedia — Ingress gagal diaktifkan"
  - Insight boxes: Bottleneck #1, #2, HPA effect, tunnel prediction
  - Summary stats cards
- `generate_cost_analysis()`: Kalkulasi On-Premise vs GCP vs GCP+HPA selama 5 tahun (listrik, admin, penyusutan server)

---

## Ingress Resource (L7 Load Balancer)

Definisi Ingress di `sync.py`:263 (`lms-setup.yaml` bagian 7):

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: moodle-ingress
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /
spec:
  ingressClassName: nginx
  rules:
  - http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: moodle-service
            port:
              number: 80
```

### Cara kerja L7 Ingress di Skenario C

1. **`ensure_ingress()`:108** — Di Minikube, Ingress Controller bukan resource Kubernetes biasa, melainkan **addon** yang menjalankan Pod Nginx Ingress Controller di namespace `ingress-nginx`
   - `minikube addons enable ingress` → deploy `ingress-nginx-controller` sebagai Deployment
   - `kubectl wait --namespace=ingress-nginx --for=condition=ready pod --selector=app.kubernetes.io/component=controller`
   - Controller ini listen di **port 80 Minikube VM** dan meneruskan trafik berdasarkan resource Ingress

2. **Resource Ingress `moodle-ingress`** — Didefinisikan di `sync.py` dan di-apply sebagai bagian dari manifest:
   - `ingressClassName: nginx` — menunjuk ke Nginx Ingress Controller
   - `path: /` dengan `pathType: Prefix` — semua request dikirim ke `moodle-service:80`
   - `rewrite-target: /` — rewrite path sebelum masuk ke pod

3. **`setup_ingress_connection()`:160** — Tidak menggunakan port-forward sama sekali:
   - `minikube ip` → mendapatkan IP Minikube VM (misal `192.168.49.2`)
   - Return `http://{ip}` sebagai target host
   - Nginx Ingress Controller sudah otomatis listen di port 80 IP tersebut
   - Proc = `None` (tidak perlu cleanup)

4. **Aliran trafik**:
   ```
   Locust --> http://{minikube_ip}:80
              |
              v
       [Nginx Ingress Controller]   <-- minikube addon
              |  (L7 routing berdasarkan Ingress rule)
              v
       [moodle-service:80]          <-- ClusterIP Service (L4)
              |  (round-robin ke pod)
              v
       [moodle-deployment pod]      <-- nginx:80 + stress:5000
   ```

5. **Keuntungan L7 Ingress vs port-forward**:
   - Tidak ada bottleneck proxy lokal — Nginx Ingress Controller berjalan di dalam klaster
   - L7 routing (path-based, header-based) — meski di sini hanya path `/`
   - TLS termination bisa ditambahkan
   - **Tapi tetap** melalui Service (ClusterIP) → L4 round-robin, bukan bypass

## Arsitektur Pod

Setiap pod terdiri dari 2 container:

### Container 1: `nginx:alpine` (Moodle)
- Server HTTP yang menyajikan halaman moodle statis
- Port 80
- 3 endpoint: `/` (index), `/tugas.html`, `/api/courses` (fake API delay 200ms)

### Container 2: `python:3.11-alpine` (Stress Sidecar)
- Menjalankan `stress.py` dengan `ProcessPoolExecutor(4)`
- Setiap request HTTP ke sidecar memicu 15.000 iterasi SHA-256
- Sidecar dipanggil inline dari nginx via `subrequest` ke `http://localhost:5000/stress`
- **Tujuan**: meniru CPU load realistis agar HPA bereaksi

### HPA Configuration
- `metrics.k8s.io/v1` — target CPU 30%
- `minReplicas: 1`, `maxReplicas: 10`
- `behavior.scaleUp.stabilizationWindowSeconds: 0` — agresif
- `behavior.scaleUp.policies[0]: periodSeconds: 15, value: 3, type: Pods`

## Daftar port

| Port | Kegunaan | Mode |
|---|---|---|
| 8080 | Port-forward ke Service (L4) | Skenario A/B/C - L4 Service |
| 8081 | Port-forward ke Pod tertentu (Direct) | Skenario C - Direct Pod |
| 8089 | Locust Web UI | Skenario 3 Interaktif |
| 80 | Port nginx di dalam pod + port listen Ingress Controller | Semua |
| 5000 | Port stress sidecar (internal pod) | Semua |
| 80 (Minikube IP) | Nginx Ingress Controller L7 — langsung akses tanpa port-forward | Skenario C - L7 Ingress |

---

## Variabel penting (constants)

| Konstanta | Nilai | Lokasi |
|---|---|---|
| `MINIKUBE_CPUS` | 6 | :254 |
| `MINIKUBE_MEMORY` | 7168 MB (7 GB) | :255 |
| `MINIKUBE_DISK` | 50000 MB (50 GB) | :256 |
| `MINIKUBE_RUNTIME` | containerd | :257 |
| `PORT_FORWARD_PORT` | 8080 | :265 |
| `MINIKUBE_SSH_PORT` | 8081 | :269 |
| `INGRESS_HOST` | moodle.local | :65 |
| `DEFAULT_LOCUST_USERS` | 500 | :253 |
| `DEFAULT_SPAWN_RATE` | 50 | :252 |
| `DEFAULT_RUN_TIME` | 5m | :251 |
| `COMP_USERS` | 150 | :82 (Skenario C) |
| `COMP_SPAWN` | 15 | :83 (Skenario C) |
| `COMP_TIME` | 3m | :84 (Skenario C) |
| `COMP_PREFLIGHT_RETRIES` | 5 | :85 (Skenario C) |
| `MODE_POD_DIRECT` | "pod_direct" | :30 (Skenario C) |
| `MODE_SERVICE_L4` | "service_l4" | :31 (Skenario C) |
| `MODE_INGRESS_L7` | "ingress_l7" | :32 (Skenario C) |
| `TUNNEL_TIMEOUT` | 15 | :262 (Skenario A/B) |
| `PREFLIGHT_RETRIES` | 3 | :263 (Skenario A/B) |
