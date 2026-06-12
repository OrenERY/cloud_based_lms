from locust import HttpUser, task, between

class LMSUser(HttpUser):
    wait_time = between(1, 2)
    connection_timeout = 30.0
    network_timeout = 30.0

    @task(3)
    def view_dashboard(self):
        self.client.get("/", timeout=10)

    @task(3)
    def view_tugas(self):
        self.client.get("/tugas.html", timeout=10)

    @task(5)
    def fetch_courses_api(self):
        self.client.get("/api/courses", timeout=30)

    @task(1)
    def health_check(self):
        self.client.get("/health", timeout=10)
