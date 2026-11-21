import json, socket, sys, time
mode, path = sys.argv[1], f"/ipc/{sys.argv[2]}.sock"
while True:
    try:
        s = socket.socket(socket.AF_UNIX); s.connect(path); break
    except OSError:
        time.sleep(0.05)
reader = s.makefile("rb")
while True:
    line = reader.readline()
    if not line:
        break
    if mode == "hang":
        time.sleep(3600)          # never answers
    time.sleep(1.5)               # slow but active policy
    s.sendall(json.dumps({"name": "get_record", "args": {"table": "ledger", "id": "row-01"}}).encode() + b"\n")
time.sleep(3600)
