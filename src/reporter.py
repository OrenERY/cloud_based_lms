"""
Parsing CSV hasil Locust dan generasi laporan HTML menggunakan Jinja2 templates.
"""
import os
import csv
import json

from jinja2 import Environment, FileSystemLoader

from .config import (
    MODE_POD_DIRECT, MODE_SERVICE_L4, MODE_INGRESS_L7,
    RESULT_DIR, TEMPLATES_DIR,
)


# ============================================================
#  CSV PARSING
# ============================================================

def parse_locust_csv(csv_stem):
    """Parse Locust stats CSV dan ambil metrics agregat."""
    path = f"{csv_stem}_stats.csv"
    if not os.path.exists(path):
        return None
    try:
        agg = {"avg_ms": 0, "p95_ms": 0, "fail_pct": 0, "rps": 0, "count": 0}
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("Name") == "Aggregated":
                    agg["avg_ms"] = round(float(row.get("Average Response Time", 0)), 1)
                    agg["p95_ms"] = round(float(row.get("95%", 0)), 1)
                    req_count = int(row.get("Request Count", 0))
                    fail_count = int(row.get("Failure Count", 0))
                    agg["fail_pct"] = round((fail_count / req_count * 100) if req_count else 0, 2)
                    agg["rps"] = round(float(row.get("Requests/s", 0)), 1)
                    agg["count"] = req_count
                    break
            if agg["count"] == 0:
                # fallback: jumlah dari semua baris non-aggregated
                total = 0
                for row in reader:
                    total += int(row.get("Request Count", 0))
                agg["count"] = total
        return agg
    except Exception:
        return None


def parse_timeseries_csv(csv_stem):
    """Parse Locust stats_history CSV untuk data time-series."""
    path = f"{csv_stem}_stats_history.csv"
    if not os.path.exists(path):
        return []
    series = []
    try:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if len(row) < 23:
                    continue
                try:
                    users = int(float(row[1]))
                    avg_rt = round(float(row[20]))
                    max_rt = round(float(row[22]))
                    series.append({"users": users, "avg": avg_rt, "max": max_rt})
                except (ValueError, IndexError):
                    pass
                if users >= 500:
                    break
    except Exception:
        pass
    return series


# ============================================================
#  REPORT GENERATION
# ============================================================

def generate_comparison_report(results, comp_users, comp_time):
    """Buat result/perbandingan.html menggunakan Jinja2 template."""
    # Validity badges (plain text)
    badge_valid = '<b>[Valid]</b> via tunnel'
    badge_limited = '<b>[Terbatas]</b> via port-forward'

    rows = []
    has_ingress = any(r["mode"] == MODE_INGRESS_L7 for r in results)

    for r in results:
        agg = parse_locust_csv(r["csv"])
        label_hpa = "Tanpa HPA" if r["scenario"] == "tanpa_hpa" else "Dengan HPA"
        label_lb = {
            MODE_POD_DIRECT: "Tanpa LB (Direct Pod)",
            MODE_SERVICE_L4: "LB L4 (Service)",
            MODE_INGRESS_L7: "LB L7 (Ingress)"
        }.get(r["mode"], r["mode"])
        avg = f"{agg['avg_ms']} ms" if agg else "—"
        p95 = f"{agg['p95_ms']} ms" if agg else "—"
        fail = f"{agg['fail_pct']}%" if agg else "—"
        rps = f"{agg['rps']} req/s" if agg else "—"

        # Validitas per-mode
        is_limited = r.get("is_limited", True)
        validity = badge_limited if is_limited else badge_valid

        rows.append({
            "lb": label_lb, "hpa": label_hpa,
            "avg": avg, "p95": p95, "fail": fail, "rps": rps,
            "avg_raw": agg['avg_ms'] if agg else 0,
            "fail_raw": agg['fail_pct'] if agg else 0,
            "rps_raw": agg['rps'] if agg else 0,
            "validity": validity,
            "is_limited": is_limited
        })

    # Time-series dari skenario pertama yang memiliki data
    ts_data = []
    for r in results:
        if r["csv"]:
            ts_data = parse_timeseries_csv(r["csv"])
            if ts_data:
                break

    # Time-series table (sampled)
    ts_table = []
    if ts_data:
        step = max(1, len(ts_data) // 12)
        for p in ts_data[::step]:
            ts_table.append(p)
        last = ts_data[-1]
        if last['users'] not in [p['users'] for p in ts_table]:
            ts_table.append(last)

    # Connection mode label
    all_limited = all(r.get("is_limited", True) for r in results)
    any_limited = any(r.get("is_limited", True) for r in results)
    if all_limited:
        conn_mode = "Port-Forward (terbatas)"
    elif any_limited:
        conn_mode = "Campuran (tunnel + port-forward)"
    else:
        conn_mode = "Tunnel (throughput penuh)"

    # Chart data
    chart_labels = [f"{rr['lb']} ({rr['hpa']})" for rr in rows]
    chart_avg = [rr['avg_raw'] for rr in rows]
    chart_fail = [rr['fail_raw'] for rr in rows]
    chart_rps = [rr['rps_raw'] for rr in rows]
    ts_users = [p['users'] for p in ts_data]
    ts_avg = [p['avg'] for p in ts_data]
    ts_max = [p['max'] for p in ts_data]

    # Render template
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=False)
    template = env.get_template("report_template.html")

    html = template.render(
        comp_users=comp_users,
        comp_time=comp_time,
        conn_mode=conn_mode,
        rows=rows,
        has_ingress=has_ingress,
        ts_table=ts_table,
        chart_labels=chart_labels,
        chart_avg=chart_avg,
        chart_fail=chart_fail,
        chart_rps=chart_rps,
        ts_users=ts_users,
        ts_avg=ts_avg,
        ts_max=ts_max,
    )

    os.makedirs(RESULT_DIR, exist_ok=True)
    output_path = os.path.join(RESULT_DIR, "perbandingan.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("   result/perbandingan.html — Laporan perbandingan LB (enhanced)")


def generate_cost_analysis():
    """Buat result/analisis_biaya.html dari template."""
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=False)
    template = env.get_template("cost_template.html")
    html = template.render()

    os.makedirs(RESULT_DIR, exist_ok=True)
    output_path = os.path.join(RESULT_DIR, "analisis_biaya.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print("   result/analisis_biaya.html — Laporan analisis biaya")
