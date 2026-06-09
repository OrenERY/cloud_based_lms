from locust import HttpUser, task, between

class LMSUser(HttpUser):
    # Jeda waktu dipercepat untuk simulasi 500 concurrent users
    wait_time = between(0.5, 1.5)

    @task(3)
    def view_dashboard(self):
        """Mahasiswa mengakses halaman Dasbor utama"""
        self.client.get("/")

    @task(2)
    def view_tugas(self):
        """Mahasiswa mengakses halaman pengumpulan tugas"""
        self.client.get("/tugas.html")

    @task(8)
    def fetch_courses_api(self):
        """Aplikasi mengambil data kursus via API (CPU stress – triggers HPA)"""
        self.client.get("/api/courses")

    @task(1)
    def health_check(self):
        """Koneksi berkala ke endpoint health check"""
        self.client.get("/health")

