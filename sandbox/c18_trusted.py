import json, sys, time
from pathlib import Path
sys.path.insert(0, "/app")
from judge_adapter import make_env, score_episode
from genesis_audit.runner.remote_policy import PolicyListener
from genesis_audit.runner.session import run_episode
name = sys.argv[1]
sock = Path(f"/ipc/{name}.sock"); sock.unlink(missing_ok=True)
rows = []
with PolicyListener(sock) as listener, listener.episode("reconciliation") as choose:
    t = time.monotonic()
    env = make_env()
    result = run_episode(env, episode_dir=sorted(Path("/app/episodes/train").glob("*/manifest.json"))[0].parent, seed=0,
                         choose=choose, score=score_episode, record=rows.append)
    wall = time.monotonic() - t
print(json.dumps({"scenario": name, "wall_seconds": round(wall, 2), **result,
                  "frozen_findings": env.outcome()["findings"],
                  "last_observation": rows[-1]["observation"], "nonzero_rewards": sum(r["reward"] != 0 for r in rows)}))
