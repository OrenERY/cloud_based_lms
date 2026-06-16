"""
Sinkronisasi file HTML/CSS lokal ke manifes Kubernetes dan deploy ke cluster.
Membaca template dari k8s/ directory, menggabungkannya, dan menerapkan ke klaster.
"""
import sys
import subprocess
import os

K8S_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "k8s")
OUTPUT_FILE = "lms-setup.yaml"


def indent(text, spaces=4):
    """Indent each line of text by given spaces."""
    return '\n'.join(' ' * spaces + line if line.strip() else line for line in text.splitlines())


def read_file(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


# Baca file statis lokal (TIDAK DIMODIFIKASI — hanya dibaca)
print("Membaca file lokal...")
try:
    index_content = read_file('index.html')
    tugas_content = read_file('tugas.html')
    style_content = read_file('style.css')
except Exception as e:
    print(f"Error: gagal membaca file lokal: {e}")
    sys.exit(1)

# Baca dan gabungkan semua template dari k8s/ directory
print(f"Membaca template dari {K8S_DIR}/...")
try:
    k8s_files = sorted(f for f in os.listdir(K8S_DIR) if f.endswith('.yaml'))
    if not k8s_files:
        print(f"Error: tidak ada file YAML di {K8S_DIR}/")
        sys.exit(1)

    parts = []
    for fname in k8s_files:
        content = read_file(os.path.join(K8S_DIR, fname))
        parts.append(f"# === {fname} ===\n{content}")
    template = "\n---\n".join(parts)
except Exception as e:
    print(f"Error: gagal membaca template dari k8s/: {e}")
    sys.exit(1)

# Replace placeholders with indented content
manifest = template.replace("{{INDEX_HTML}}", indent(index_content, 4))
manifest = manifest.replace("{{TUGAS_HTML}}", indent(tugas_content, 4))
manifest = manifest.replace("{{STYLE_CSS}}", indent(style_content, 4))

try:
    with open(OUTPUT_FILE, 'w', encoding='utf-8', newline='') as f:
        f.write(manifest)
    print(f"{OUTPUT_FILE} berhasil diperbarui.")
except Exception as e:
    print(f"Error: gagal menulis {OUTPUT_FILE}: {e}")
    sys.exit(1)

# Apply to cluster
print("Menerapkan manifes ke klaster...")
try:
    subprocess.run(["kubectl", "apply", "-f", OUTPUT_FILE], check=True)
    print("Merestart pod deployment...")
    subprocess.run(["kubectl", "rollout", "restart", "deployment", "moodle-deployment"], check=True)
    print("Sinkronisasi & deployment sukses!")
except Exception as e:
    print(f"Error: gagal menjalankan perintah Kubernetes: {e}")
    sys.exit(1)
