"""Run INSIDE the policy container: try to reach everything a policy must not."""
import importlib, json, os, socket

report = {}


def attempt(name, fn):
    try:
        report[name] = {"allowed": True, "detail": str(fn())[:200]}
    except Exception as exc:
        report[name] = {"allowed": False, "detail": f"{type(exc).__name__}: {exc}"[:200]}


report["uid_gid"] = f"{os.getuid()}:{os.getgid()}"
status = dict(line.split(":", 1) for line in open("/proc/self/status") if ":" in line)
report["CapEff"] = status["CapEff"].strip()
report["NoNewPrivs"] = status["NoNewPrivs"].strip()
report["visible_pids"] = sorted(int(p) for p in os.listdir("/proc") if p.isdigit())
report["net_interfaces"] = sorted(os.listdir("/sys/class/net"))

attempt("tcp_connect_1.1.1.1:443", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3))
attempt("dns_resolve_example.com", lambda: socket.getaddrinfo("example.com", 443))
attempt("udp_send_8.8.8.8:53", lambda: socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("8.8.8.8", 53)))
attempt("docker_socket", lambda: os.stat("/var/run/docker.sock"))
attempt("write_/app", lambda: open("/app/pwn.txt", "w").write("x"))
attempt("write_/", lambda: open("/pwn.txt", "w").write("x"))
attempt("write_/ipc", lambda: open("/ipc/pwn.txt", "w").write("x"))

for module in ("genesis_audit.env", "genesis_audit.scoring", "genesis_audit.data",
               "genesis_audit.runner", "judge_adapter", "genesis_audit.contracts.files"):
    attempt(f"import_{module}", lambda m=module: importlib.import_module(m))

secret_names = {"truth.json", "provenance.json", "manifest.json", "ledger.csv",
                "payments.jsonl", "invoices.jsonl", "expected_findings.json", "judge_adapter.py"}
hits = []
for root, dirs, files in os.walk("/", onerror=lambda e: None):
    if root.startswith(("/proc", "/sys", "/dev")):
        dirs[:] = []
        continue
    for name in files:
        if name in secret_names or "episode" in name.lower():
            hits.append(os.path.join(root, name))
report["episode_or_secret_files_found"] = hits
report["app_tree"] = sorted(os.path.join(r, f)[len("/app/"):] for r, _, fs in os.walk("/app") for f in fs)
report["ipc_listing"] = sorted(os.listdir("/ipc")) if os.path.isdir("/ipc") else None
report["env_vars"] = sorted(k for k in os.environ)
print(json.dumps(report, indent=1))
