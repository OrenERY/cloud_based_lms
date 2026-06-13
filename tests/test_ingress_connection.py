import sys, os, time, subprocess, json, tempfile

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_capture(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def health_check(url, timeout_sec=5):
    import urllib.request
    try:
        r = urllib.request.urlopen(url, timeout=timeout_sec)
        return r.status == 200
    except Exception:
        return False

def _patch_svc_type(name, svc_type, namespace="default"):
    pf = os.path.join(tempfile.gettempdir(), f"test_patch_{name}.json")
    with open(pf, "w") as f:
        json.dump({"spec": {"type": svc_type}}, f)
    subprocess.run(["kubectl", "patch", "svc", name, "-n", namespace, "--patch-file", pf],
                   timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

print("=" * 60)
print("TEST: Koneksi Ingress (L7)")
print("=" * 60)

print("\n[1] Test setup_ingress_connection logic...")

# Step 1: patch ingress-nginx-controller to LoadBalancer
print("  Patch ingress-nginx-controller -> LoadBalancer...")
_patch_svc_type("ingress-nginx-controller", "LoadBalancer", "ingress-nginx")
time.sleep(3)

# Step 2: patch moodle-service to ClusterIP
print("  Patch moodle-service -> ClusterIP...")
_patch_svc_type("moodle-service", "ClusterIP")
time.sleep(3)

# Step 3: poll tunnel
print("  Polling 127.0.0.1:80 (max 30 detik)...")
success = False
for attempt in range(10):
    if health_check("http://127.0.0.1"):
        print(f"\n  [OK] Ingress siap via tunnel! (percobaan {attempt+1})")
        success = True
        break
    sys.stdout.write(f"\r    Menunggu... ({attempt+1}/10)")
    sys.stdout.flush()
    time.sleep(3)

print()

if not success:
    print("  [FAIL] Gagal terhubung ke Ingress via tunnel")

# Verify moodle-service is ClusterIP
r = run_capture(["kubectl", "get", "svc", "moodle-service", "-o", "jsonpath={.spec.type}"])
print(f"  moodle-service type: {r.stdout.strip()}")

r = run_capture(["kubectl", "get", "svc", "-n", "ingress-nginx", "ingress-nginx-controller", "-o", "jsonpath={.spec.type}"])
print(f"  ingress-nginx-controller type: {r.stdout.strip()}")

# Restore moodle-service
_patch_svc_type("moodle-service", "LoadBalancer")
time.sleep(3)
r = run_capture(["kubectl", "get", "svc", "moodle-service", "-o", "jsonpath={.spec.type}"])
print(f"  (restored) moodle-service type: {r.stdout.strip()}")

print("\n" + "=" * 60)
if success:
    print("[PASS] TEST L7 INGRESS SUKSES!")
else:
    print("[FAIL] TEST L7 INGRESS GAGAL")
print("=" * 60)
