"""
Konfigurasi pusat untuk LMS UNSAP Kubernetes HPA Load Test.
Semua konstanta, path, dan default values dikumpulkan di sini.
"""
import os

# ============================================================
#  MINIKUBE CLUSTER
# ============================================================
MINIKUBE_CPUS = "6"
MINIKUBE_MEMORY = "7000"
MINIKUBE_DISK = "20g"
MINIKUBE_RUNTIME = "containerd"

# ============================================================
#  NETWORKING / PORTS
# ============================================================
PORT_FORWARD_PORT = 8080       # Port-forward ke Service (L4)
POD_DIRECT_PORT = 8081         # Port-forward ke Pod langsung
TUNNEL_TIMEOUT = 15            # Detik menunggu tunnel sebelum fallback
PREFLIGHT_RETRIES = 10         # Percobaan koneksi sebelum mulai test

# ============================================================
#  MODE KONEKSI (Skenario Perbandingan)
# ============================================================
MODE_POD_DIRECT = "pod_direct"   # port-forward ke 1 pod = tanpa LB
MODE_SERVICE_L4 = "service_l4"   # port-forward ke Service = L4 kube-proxy
MODE_INGRESS_L7 = "ingress_l7"   # via Nginx Ingress = L7 proxy

# ============================================================
#  LOAD TEST DEFAULTS
# ============================================================
DEFAULT_USERS = 500
DEFAULT_SPAWN = 10
DEFAULT_TIME = "5m"

# Konfigurasi bawaan untuk perbandingan otomatis (Skenario C)
COMP_USERS = 150
COMP_SPAWN = 5
COMP_TIME = "3m"

# ============================================================
#  PATHS
# ============================================================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K8S_DIR = os.path.join(BASE_DIR, "k8s")
RESULT_DIR = os.path.join(BASE_DIR, "result")
CSV_DIR = os.path.join(RESULT_DIR, "csv")
MANIFEST_FILE = os.path.join(BASE_DIR, "lms-setup.yaml")
LOCUSTFILE = os.path.join(BASE_DIR, "locustfile.py")
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
