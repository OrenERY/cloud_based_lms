from locust import HttpUser, task, between

class LMSUser(HttpUser):
    # Jeda waktu antar-request per user
    wait_time = between(1, 2)

    # Timeout 30 detik agar request yang lambat tidak hang selamanya.
    # Tanpa ini, request yang timeout akan menggantung dan menghasilkan
    # response time palsu (~4100ms) alih-alih error yang jelas.
    connection_timeout = 30.0
    network_timeout = 30.0

    @task(3)
    def view_dashboard(self):
        """Mahasiswa mengakses halaman Dasbor utama"""
        self.client.get("/", timeout=10)

    @task(3)
    def view_tugas(self):
        """Mahasiswa mengakses halaman pengumpulan tugas"""
        self.client.get("/tugas.html", timeout=10)

    @task(5)
    def fetch_courses_api(self):
        """Aplikasi mengambil data kursus via API (CPU stress – triggers HPA)"""
        self.client.get("/api/courses", timeout=30)

    @task(1)
    def health_check(self):
        """Koneksi berkala ke endpoint health check"""
        self.client.get("/health", timeout=10)
