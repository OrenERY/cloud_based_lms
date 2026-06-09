import sys
import subprocess

def indent(text, spaces=4):
    return '\n'.join(' ' * spaces + line if line.strip() else line for line in text.splitlines())

print("[INFO] Membaca file lokal...")
try:
    with open('index.html', 'r', encoding='utf-8') as f:
        index_content = f.read()
    with open('tugas.html', 'r', encoding='utf-8') as f:
        tugas_content = f.read()
    with open('style.css', 'r', encoding='utf-8') as f:
        style_content = f.read()
except Exception as e:
    print(f"[ERROR] Gagal membaca file lokal: {e}")
    sys.exit(1)

yaml_template = f"""# =========================================================
#  LMS UNSAP – Prototipe Simulasi Kubernetes HPA
#  File: lms-setup.yaml
#  Deskripsi: Deploy nginx ringan yang menyajikan tiruan
#             LMS UNSAP via ConfigMap. HPA memantau CPU.
# =========================================================

# ── 1. ConfigMap: index.html (Dashboard) ─────────────────
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-html-index
data:
  index.html: |
{indent(index_content, 4)}

---
# ── 2. ConfigMap: tugas.html (Halaman Pengumpulan Tugas) ──
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-html-tugas
data:
  tugas.html: |
{indent(tugas_content, 4)}

---
# ── 3. ConfigMap: style.css (Stylesheet) ───────────────────
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-css-style
data:
  style.css: |
{indent(style_content, 4)}

---
# ── 4. ConfigMap: nginx.conf (main config tuning) ─────────
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-nginx-main-conf
data:
  nginx.conf: |
    worker_processes auto;
    events {{
        worker_connections 2048;
        use epoll;
    }}
    http {{
        include       /etc/nginx/mime.types;
        default_type  application/octet-stream;
        sendfile        on;
        keepalive_timeout  65;
        include /etc/nginx/conf.d/*.conf;
    }}

---
# ── 4b. ConfigMap: nginx default.conf (multi-page routing) ─
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-nginx-conf
data:
  default.conf: |
    server {{
        listen 80;
        server_name localhost;
        root /usr/share/nginx/html;
        index index.html;

        # Gzip dimatikan agar CPU fokus ke stress endpoint
        gzip off;

        location / {{
            try_files $uri $uri/ /index.html;
        }}

        # Health check endpoint – dipakai Locust & k8s probe
        location /health {{
            access_log off;
            return 200 "OK\\n";
            add_header Content-Type text/plain;
        }}

        # Proxy ke sidecar Python stress endpoint
        location /api/courses {{
            proxy_pass http://127.0.0.1:5000/api/courses;
            proxy_set_header Host $host;
        }}
    }}

---
# ── 4c. ConfigMap: CPU Stress Script (Python sidecar) ──────
apiVersion: v1
kind: ConfigMap
metadata:
  name: lms-stress-script
data:
  stress_server.py: |
    #!/usr/bin/env python3
    import hashlib, json, time
    from http.server import HTTPServer, BaseHTTPRequestHandler

    class StressHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/courses":
                # CPU-intensive: hashing loop untuk memicu HPA
                data = "LMS-UNSAP-stress-payload"
                for i in range(80000):
                    data = hashlib.sha256(data.encode()).hexdigest()
                resp = json.dumps({{
                    "status": "ok",
                    "hash_rounds": 80000,
                    "courses": ["Cloud Computing", "PKS-I", "Matematika Diskrit"]
                }})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.write = self.wfile.write
                self.wfile.write(resp.encode())
            else:
                self.send_response(404)
                self.end_headers()
        def log_message(self, format, *args):
            pass  # suppress logging for performance

    if __name__ == "__main__":
        server = HTTPServer(("0.0.0.0", 5000), StressHandler)
        print("Stress server running on :5000")
        server.serve_forever()

---
# ── 5. Deployment ──────────────────────────────────────────
apiVersion: apps/v1
kind: Deployment
metadata:
  name: moodle-deployment
  labels:
    app: moodle-app
spec:
  replicas: 1
  selector:
    matchLabels:
      app: moodle-app
  template:
    metadata:
      labels:
        app: moodle-app
    spec:
      containers:
        - name: moodle
          image: nginx:alpine
          ports:
            - containerPort: 80
          resources:
            requests:
              cpu: "150m"   # Dinaikkan dari 100m
            limits:
              cpu: "400m"   # Dinaikkan dari 300m
          volumeMounts:
            - name: html-index
              mountPath: /usr/share/nginx/html/index.html
              subPath: index.html
            - name: html-tugas
              mountPath: /usr/share/nginx/html/tugas.html
              subPath: tugas.html
            - name: css-style
              mountPath: /usr/share/nginx/html/style.css
              subPath: style.css
            - name: nginx-conf
              mountPath: /etc/nginx/conf.d/default.conf
              subPath: default.conf
            - name: nginx-main-conf
              mountPath: /etc/nginx/nginx.conf
              subPath: nginx.conf
          readinessProbe:
            httpGet:
              path: /health
              port: 80
            initialDelaySeconds: 5
            periodSeconds: 5
          livenessProbe:
            httpGet:
              path: /health
              port: 80
            initialDelaySeconds: 10
            periodSeconds: 10
        # Sidecar: Python CPU stress server
        - name: stress-sidecar
          image: python:3.11-alpine
          command: ["python3", "/scripts/stress_server.py"]
          ports:
            - containerPort: 5000
          resources:
            requests:
              cpu: "50m"
            limits:
              cpu: "200m"
          volumeMounts:
            - name: stress-script
              mountPath: /scripts/stress_server.py
              subPath: stress_server.py
      volumes:
        - name: html-index
          configMap:
            name: lms-html-index
        - name: html-tugas
          configMap:
            name: lms-html-tugas
        - name: css-style
          configMap:
            name: lms-css-style
        - name: nginx-conf
          configMap:
            name: lms-nginx-conf
        - name: nginx-main-conf
          configMap:
            name: lms-nginx-main-conf
        - name: stress-script
          configMap:
            name: lms-stress-script

---
# ── 6. Service ─────────────────────────────────────────────
apiVersion: v1
kind: Service
metadata:
  name: moodle-service
spec:
  type: LoadBalancer
  ports:
    - port: 80
      targetPort: 80
      protocol: TCP
  selector:
    app: moodle-app

---
# ── 7. HorizontalPodAutoscaler ─────────────────────────────
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: moodle-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: moodle-deployment
  minReplicas: 1
  maxReplicas: 5
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 50   # Scale-up jika CPU > 50%
"""

try:
    with open('lms-setup.yaml', 'w', encoding='utf-8') as f:
        f.write(yaml_template)
    print("[INFO] lms-setup.yaml berhasil diperbarui.")
except Exception as e:
    print(f"[ERROR] Gagal memperbarui lms-setup.yaml: {e}")
    sys.exit(1)

try:
    print("[INFO] Menerapkan manifest terbaru ke Kubernetes...")
    subprocess.run(["kubectl", "apply", "-f", "lms-setup.yaml"], check=True)
    print("[INFO] Merestart pod deployment...")
    subprocess.run(["kubectl", "rollout", "restart", "deployment", "moodle-deployment"], check=True)
    print("[SUCCESS] Sinkronisasi & Deployment sukses!")
except Exception as e:
    print(f"[ERROR] Gagal menjalankan perintah Kubernetes: {e}")
    sys.exit(1)
