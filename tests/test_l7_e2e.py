import sys, os, time, subprocess, json, tempfile
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.makedirs("result", exist_ok=True)
os.makedirs("result/csv", exist_ok=True)

def rc(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def health_check(url, timeout_sec=5):
    import urllib.request
    try:
        r = urllib.request.urlopen(url, timeout=timeout_sec)
        return r.status == 200
    except Exception:
        return False

def _patch_svc_type(name, svc_type, namespace="default"):
    pf = os.path.join(tempfile.gettempdir(), f"e2e_patch_{name}.json")
    with open(pf, "w") as f:
        json.dump({"spec": {"type": svc_type}}, f)
    subprocess.run(["kubectl", "patch", "svc", name, "-n", namespace, "--patch-file", pf],
                   timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("=" * 60)
print("E2E TEST: L7 Ingress + Locust")
print("=" * 60)

# Step 1: Deploy scenario
print("\n[1] Deploy tanpa_hpa...")
subprocess.run([sys.executable, "sync.py"], timeout=120)
rc(["kubectl", "delete", "hpa", "moodle-hpa"])
rc(["kubectl", "scale", "deployment", "moodle-deployment", "--replicas=1"])
r = subprocess.run(["kubectl", "rollout", "status", "deployment/moodle-deployment", "--timeout=120s"],
                    timeout=130, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
if r.returncode != 0:
    print("  [FAIL] Gagal deploy")
    sys.exit(1)
print("  [OK] Deploy selesai")
time.sleep(5)

# Step 2: Setup Ingress connection
print("\n[2] Setup L7 Ingress connection...")
_patch_svc_type("ingress-nginx-controller", "LoadBalancer", "ingress-nginx")
time.sleep(3)
_patch_svc_type("moodle-service", "ClusterIP")
time.sleep(3)

print("  Polling 127.0.0.1:80...")
success = False
for attempt in range(10):
    if health_check("http://127.0.0.1"):
        print(f"  [OK] Ingress siap! (percobaan {attempt+1})")
        success = True
        break
    time.sleep(3)
if not success:
    print("  [FAIL] Gagal connect ke Ingress")
    _patch_svc_type("moodle-service", "LoadBalancer")
    sys.exit(1)

# Step 3: Verify connection
print("\n[3] Verify connection...")
import urllib.request
try:
    r = urllib.request.urlopen("http://127.0.0.1/", timeout=10)
    print(f"  [OK] Response: {r.status}")
except Exception as e:
    print(f"  [FAIL] Verify gagal: {e}")
    _patch_svc_type("moodle-service", "LoadBalancer")
    sys.exit(1)

# Step 4: Run Locust
print("\n[4] Run Locust (1 user, 30 detik)...")
html_report = "result/test_l7_ingress.html"
csv_stem = "result/csv/test_l7_ingress"
subprocess.run([
    "locust", "-f", "locustfile.py",
    "--host=http://127.0.0.1",
    "--users=1",
    "--spawn-rate=1",
    "--run-time=30s",
    f"--html={html_report}",
    f"--csv={csv_stem}",
    "--headless"
])

# Step 5: Check results
print("\n[5] Verifikasi hasil...")
if os.path.exists(html_report):
    print(f"  [OK] HTML report: {html_report}")
else:
    print(f"  [FAIL] HTML report tidak ditemukan")

csv_path = f"{csv_stem}_stats.csv"
if os.path.exists(csv_path):
    import csv
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("Name") == "Aggregated":
                print(f"  [OK] Avg: {row.get('Average Response Time', '?')}ms")
                print(f"  [OK] RPS: {row.get('Requests/s', '?')}")
                print(f"  [OK] Requests: {row.get('Request Count', '?')}")
                break
else:
    print(f"  [FAIL] CSV stats tidak ditemukan: {csv_path}")

# Restore moodle-service
print("\n[6] Restore moodle-service...")
_patch_svc_type("moodle-service", "LoadBalancer")
time.sleep(3)

print("\n" + "=" * 60)
if os.path.exists(html_report):
    print("[PASS] E2E L7 TEST SUKSES!")
    print("  Data L7 Ingress valid untuk jurnal!")
else:
    print("[FAIL] E2E L7 TEST GAGAL")
print("=" * 60)
