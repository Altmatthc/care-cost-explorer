#!/usr/bin/env python3
"""
Rolling multi-state program: advance ONE step per tick, unattended.

    python scripts/next_state.py            # one tick (what the cron runs)
    python scripts/next_state.py --show     # print program state, change nothing
    python scripts/next_state.py --init tx  # (re)create the queue with tx in flight

State lives in logs/state-program.json (local, gitignored). Each tick:
  * a harvest / re-harvest / finish for the current state is still running  -> quiet
  * harvest finished -> launch `run_state.py <st> finish` (detached; publishes)
  * finish stopped on coherence (rc 3) -> one targeted re-harvest, then finish
    again with --ignore-coherence
  * finish failed (publish throttle etc.) -> retried next tick, 3 attempts max
  * finish ok / zero-ok state / exhausted -> record, pop the next state,
    run registry+geocode (foreground, minutes), launch its harvest (detached)
Prints a line only when something changed, so a no-agent cron can forward
stdout verbatim and stay silent otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
VENV_PY = ROOT / (".venv/Scripts/python.exe" if platform.system() == "Windows"
                  else ".venv/bin/python")
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable
STATE_PATH = LOGS / "state-program.json"
RUN_STATE = str(ROOT / "scripts" / "run_state.py")
MAX_FINISH_ATTEMPTS = 3

# Population order, minus already-published (ca, fl) and PR (skipped per user).
DEFAULT_QUEUE = ["ny", "pa", "il", "oh", "ga", "nc", "mi", "nj", "va", "wa",
                 "az", "tn", "ma", "in", "mo", "md", "wi", "co", "mn", "sc",
                 "al", "la", "ky", "or", "ok", "ct", "ut", "ia", "nv", "ar",
                 "ms", "ks", "nm", "ne", "id", "wv", "hi", "nh", "me", "mt",
                 "ri", "de", "sd", "nd", "ak", "dc", "vt", "wy"]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(st: dict) -> None:
    LOGS.mkdir(exist_ok=True)
    st["updated"] = now()
    STATE_PATH.write_text(json.dumps(st, indent=1), encoding="utf-8")


def _cmdlines() -> list[str]:
    """Command lines of live python processes. PowerShell by full path (the
    cron environment may not have it on PATH), with WMIC/tasklist fallbacks."""
    if platform.system() != "Windows":
        try:
            r = subprocess.run(["pgrep", "-af", "python"], capture_output=True, text=True, timeout=30)
            return r.stdout.splitlines()
        except Exception:
            return []
    ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    for cmd in (
        [str(ps), "-NoProfile", "-NonInteractive", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Select-Object -ExpandProperty CommandLine"],
        ["wmic", "process", "where", "name='python.exe'", "get", "CommandLine"],
    ):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.splitlines()
        except Exception:
            continue
    return []


def processes_matching(fragment: str) -> int:
    """Count live python processes whose command line contains `fragment`."""
    return sum(1 for ln in _cmdlines() if fragment in ln)


def stage_active(state: str, stage: str) -> bool:
    """Is a run_state.py <state> <stage> still running?

    Belt and braces: a live process match wins; otherwise, for stages that
    were launched through launch_detached, 'no rc file yet' means running;
    and for a harvest launched any other way, a prices log written in the
    last 20 minutes means running. Process scanning alone proved unreliable
    from the cron environment (returned nothing while the harvest ran)."""
    if processes_matching(f"run_state.py {state} {stage}"):
        return True
    rc = LOGS / f"{state}-{stage}.rc"
    log = LOGS / (f"{state}-prices.log" if stage == "prices" else f"{state}-finish.log")
    launched = LOGS / (f"{state}-prices-driver.out" if stage == "prices" else f"{state}-finish-driver.out")
    if launched.exists() and not rc.exists() and stage == "finish":
        # finish is minutes, not hours; a missing rc file 30 min after launch is a crash
        return (time.time() - launched.stat().st_mtime) < 1800
    if stage == "prices" and log.exists():
        return (time.time() - log.stat().st_mtime) < 1200
    return False


def launch_detached(args: list[str], log: Path, rc_file: Path) -> None:
    """Start a run_state.py stage that outlives this tick; rc lands in rc_file."""
    rc_file.unlink(missing_ok=True)
    cmd = [PY, str(Path(__file__).resolve()), "--_wrap", str(rc_file), "--", PY, *args]
    kw = {}
    if platform.system() == "Windows":
        kw["creationflags"] = (subprocess.CREATE_NEW_PROCESS_GROUP
                               | getattr(subprocess, "DETACHED_PROCESS", 0x8))
    else:
        kw["start_new_session"] = True
    with log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n# {now()} launched: {' '.join(args)}\n")
        subprocess.Popen(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, close_fds=True, **kw)


def wrap(rc_file: str, cmd: list[str]) -> int:
    rc = subprocess.call(cmd, cwd=ROOT)
    Path(rc_file).write_text(str(rc), encoding="utf-8")
    return rc


def read_rc(rc_file: Path):
    try:
        return int(rc_file.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def counts(state: str) -> str:
    try:
        st = json.loads((DATA / f"{state}-status.json").read_text(encoding="utf-8"))
    except Exception:
        return "no status"
    from collections import Counter
    c = Counter(v.get("status") for v in st.values())
    return f"{len(st)} hospitals: " + ", ".join(f"{k} {v}" for k, v in c.most_common())


def regions_count(state: str):
    try:
        return json.load(open(DATA / "regions.json", encoding="utf-8")).get(state, {}).get("hospitals")
    except Exception:
        return None


def stage_tail(state: str, n: int = 3) -> str:
    p = LOGS / f"{state}-stages.log"
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    return "\n".join("    " + ln for ln in lines)


# --------------------------------------------------------------------------
REFRESH_DAYS = 45   # matches merge_shards RECONFIRM_DAYS so a refresh can write reconfirm rows


def refresh_due() -> list[tuple[str, str]]:
    """Published regions whose data is REFRESH_DAYS+ old, oldest first."""
    try:
        regions = json.loads((DATA / "regions.json").read_text(encoding="utf-8"))
    except Exception:
        return []
    due = []
    for code, m in regions.items():
        upd = m.get("updated")
        if not upd:
            continue
        try:
            age = (datetime.now() - datetime.fromisoformat(upd[:10])).days
        except ValueError:
            continue
        if age >= REFRESH_DAYS and (DATA / f"{code}-hospitals.json").exists():
            due.append((code, upd))
    return sorted(due, key=lambda x: x[1])


def start_next(st: dict, out: list[str]) -> None:
    st["finish_attempts"] = 0
    st["reharvested"] = False
    # A published state whose data has aged past REFRESH_DAYS takes priority
    # over the next first-pass state: trend history only grows on re-harvest,
    # and the GitHub Actions schedule that was meant to do this never fires.
    due = refresh_due()
    if due:
        state, upd = due[0]
        st["current"] = state
        st["mode"] = "refresh"
        out.append(f"{state.upper()}: refresh (last data {upd}) — registry + incremental harvest")
        subprocess.run([PY, RUN_STATE, state, "registry"], cwd=ROOT, capture_output=True, text=True)
        launch_detached([RUN_STATE, state, "prices", "--workers", "4"],
                        LOGS / f"{state}-prices-driver.out", LOGS / f"{state}-prices.rc")
        st["phase"] = "harvest"
        st["phase_since"] = now()
        return
    st["mode"] = "first"
    if not st["queue"]:
        st["current"] = None
        st["phase"] = "complete"
        out.append("program complete — queue empty (refresh cycle continues)")
        return
    state = st["queue"].pop(0)
    st["current"] = state
    out.append(f"{state.upper()}: registry + geocode")
    r = subprocess.run([PY, RUN_STATE, state, "registry"], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        st["failed"][state] = f"registry rc={r.returncode}"
        out.append(f"{state.upper()}: registry FAILED (rc={r.returncode}) — skipping")
        out.append(stage_tail(state))
        start_next(st, out)
        return
    out.append(stage_tail(state, 2))
    launch_detached([RUN_STATE, state, "prices", "--workers", "4"],
                    LOGS / f"{state}-prices-driver.out", LOGS / f"{state}-prices.rc")
    st["phase"] = "harvest"
    st["phase_since"] = now()
    out.append(f"{state.upper()}: harvest launched (background)")


def tick(st: dict) -> list[str]:
    out: list[str] = []
    state = st.get("current")
    phase = st.get("phase")
    st.setdefault("refreshed", {})
    st.setdefault("mode", "first")
    if not state:
        # Queue drained: keep ticking for refreshes only.
        if phase != "complete" or refresh_due():
            start_next(st, out)
        return out

    if phase in ("harvest", "reharvest"):
        if stage_active(state, "prices"):
            return out
        out.append(f"{state.upper()}: harvest finished — {counts(state)}")
        args = [RUN_STATE, state, "finish"]
        if phase == "reharvest":
            args.append("--ignore-coherence")
        launch_detached(args, LOGS / f"{state}-finish-driver.out", LOGS / f"{state}-finish.rc")
        st["phase"] = "finish"
        st["phase_since"] = now()
        st["finish_attempts"] = st.get("finish_attempts", 0) + 1
        out.append(f"{state.upper()}: finish launched (merge/validate/commit/publish)")
        return out

    if phase == "finish":
        if stage_active(state, "finish"):
            return out
        rc = read_rc(LOGS / f"{state}-finish.rc")
        if rc == 0:
            entry = {"hospitals": regions_count(state), "counts": counts(state), "at": now()}
            if st.get("mode") == "refresh":
                st.setdefault("refreshed", {})[state] = entry
                out.append(f"{state.upper()}: REFRESHED — {regions_count(state)} hospitals live")
            else:
                st["done"][state] = entry
                out.append(f"{state.upper()}: PUBLISHED — {regions_count(state)} hospitals live")
            out.append(stage_tail(state, 2))
            start_next(st, out)
            return out
        if rc == 3 and not st.get("reharvested"):
            ids = (LOGS / f"{state}-reharvest-ids.txt").read_text(encoding="utf-8").strip()
            st["reharvested"] = True
            launch_detached([RUN_STATE, state, "prices", "--only", ids],
                            LOGS / f"{state}-prices-driver.out", LOGS / f"{state}-prices.rc")
            st["phase"] = "reharvest"
            st["phase_since"] = now()
            out.append(f"{state.upper()}: coherence gap — re-harvesting {ids.count(',') + 1} hospitals")
            return out
        if rc == 4:
            st["failed"][state] = "zero ok hospitals"
            out.append(f"{state.upper()}: no usable price files at all — moving on")
            start_next(st, out)
            return out
        if st.get("finish_attempts", 0) >= MAX_FINISH_ATTEMPTS:
            st["failed"][state] = f"finish rc={rc} after {MAX_FINISH_ATTEMPTS} attempts"
            out.append(f"{state.upper()}: finish FAILED (rc={rc}) {MAX_FINISH_ATTEMPTS}x — "
                       f"needs a look; moving on")
            out.append(stage_tail(state, 3))
            start_next(st, out)
            return out
        # retry (publish throttle, Pages lag...)
        launch_detached([RUN_STATE, state, "finish"] + (["--ignore-coherence"] if st.get("reharvested") else []),
                        LOGS / f"{state}-finish-driver.out", LOGS / f"{state}-finish.rc")
        st["finish_attempts"] += 1
        st["phase_since"] = now()
        out.append(f"{state.upper()}: finish rc={rc}; retry {st['finish_attempts']}/{MAX_FINISH_ATTEMPTS}")
        out.append(stage_tail(state, 2))
        return out
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--init", metavar="STATE", help="reset the program with STATE in flight (phase harvest)")
    ap.add_argument("--_wrap", metavar="RCFILE")
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args()
    if a._wrap:
        return wrap(a._wrap, a.rest)
    if a.init:
        st = {"queue": [s for s in DEFAULT_QUEUE if s != a.init], "current": a.init,
              "phase": "harvest", "phase_since": now(), "finish_attempts": 0,
              "reharvested": False, "mode": "first", "done": {}, "refreshed": {},
              "failed": {}, "started": now()}
        save_state(st)
        print(json.dumps(st, indent=1))
        return 0
    st = load_state()
    if not st:
        print("no program state — run --init <state>")
        return 1
    if a.show:
        print(json.dumps(st, indent=1))
        if st.get("current"):
            print(f"\n{st['current']}: {counts(st['current'])}")
            print(stage_tail(st["current"], 4))
        return 0
    lines = tick(st)
    save_state(st)
    if lines:
        done, left = len(st["done"]), len(st["queue"])
        print("\n".join(lines))
        print(f"[program] {done} published this program, {left} queued, "
              f"{len(st.get('refreshed', {}))} refreshed, {len(st['failed'])} flagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
