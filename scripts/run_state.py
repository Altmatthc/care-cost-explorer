#!/usr/bin/env python3
"""
One state, end to end, one stage at a time — the rolling-program driver.

    python scripts/run_state.py tx registry   # CMS registry + geocode (minutes)
    python scripts/run_state.py tx prices     # harvest (hours; run in background)
    python scripts/run_state.py tx finish     # merge -> validate -> commit -> publish -> live probe
    python scripts/run_state.py tx status     # counts + coherence, no changes

Every stage appends a line to logs/<state>-stages.log so an unattended run can
be reported stage by stage. `finish` refuses to commit while a harvest process
for the same state is still alive, and refuses to publish if the coherence
checks fail (ok hospital with no rows, priced hospital that isn't ok).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
VENV_PY = ROOT / (".venv/Scripts/python.exe" if platform.system() == "Windows"
                  else ".venv/bin/python")
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable
SITE = "https://altmatthc.github.io/care-cost-explorer"


def stage_log(state: str, msg: str) -> None:
    LOGS.mkdir(exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(f"[stage] {line}", flush=True)
    with (LOGS / f"{state}-stages.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def run(args: list[str], log: Path | None = None) -> int:
    cmd = [PY] + args
    print(f"$ {' '.join(['python'] + args)}", flush=True)
    if log:
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n$ {' '.join(cmd)}\n")
            fh.flush()
            r = subprocess.run(cmd, cwd=ROOT, stdout=fh, stderr=subprocess.STDOUT)
    else:
        r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode


def git(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def harvest_running(state: str) -> bool:
    """True if a build_data.py --region <state> process is alive, or its
    prices log was written in the last 20 minutes (process scanning can
    return nothing from a service/cron context, so the log is the backstop)."""
    lines = ""
    try:
        if platform.system() == "Windows":
            ps = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / \
                "WindowsPowerShell" / "v1.0" / "powershell.exe"
            r = subprocess.run(
                [str(ps), "-NoProfile", "-NonInteractive", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                 "Select-Object -ExpandProperty CommandLine"],
                capture_output=True, text=True, timeout=90)
            lines = r.stdout
        else:
            r = subprocess.run(["pgrep", "-af", "build_data.py"], capture_output=True, text=True)
            lines = r.stdout
    except Exception:
        lines = ""
    if any("build_data.py" in ln and f"--region {state}" in ln for ln in lines.splitlines()):
        return True
    log = LOGS / f"{state}-prices.log"
    return log.exists() and (time.time() - log.stat().st_mtime) < 1200


def status_counts(state: str) -> tuple[int, Counter]:
    st = load(DATA / f"{state}-status.json", {})
    return len(st), Counter(v.get("status") for v in st.values())


def coherence(state: str) -> tuple[list[str], list[str]]:
    """(ok-without-rows, priced-but-not-ok) hospital ids."""
    st = load(DATA / f"{state}-status.json", {})
    prices = load(DATA / f"{state}-prices.json", {})
    priced = {k.split("|")[0] for k in prices if k != "_meta"}
    ok = {hid for hid, v in st.items() if v.get("status") == "ok"}
    return sorted(ok - priced), sorted(priced - ok)


def catalogue_gap(state: str) -> list[str]:
    st = load(DATA / f"{state}-status.json", {})
    ok = {hid for hid, v in st.items() if v.get("status") == "ok"}
    cat = {p.stem for p in (DATA / f"{state}-catalogue").glob("*.json")}
    return sorted(ok - cat)


def probe(url: str) -> int:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "care-cost-explorer-probe"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


# --------------------------------------------------------------------------
def do_registry(state: str) -> int:
    log = LOGS / f"{state}-registry.log"
    stage_log(state, "registry: start")
    if run(["scripts/build_data.py", "--region", state, "--stage", "registry"], log):
        stage_log(state, "registry: FAILED (see logs)")
        return 1
    reg = load(DATA / f"{state}-hospitals.json", [])
    with_src = sum(1 for h in reg if h.get("mrf_url") or h.get("domain"))
    stage_log(state, f"registry: {len(reg)} hospitals, {with_src} with a domain/URL lead")
    stage_log(state, "geocode: start")
    if run(["scripts/build_data.py", "--region", state, "--stage", "geocode"], log):
        stage_log(state, "geocode: FAILED (see logs)")
        return 1
    reg = load(DATA / f"{state}-hospitals.json", [])
    located = sum(1 for h in reg if h.get("lat") is not None)
    stage_log(state, f"geocode: {located}/{len(reg)} located")
    return 0


def do_prices(state: str, workers: int, only: str, refresh_all: bool) -> int:
    if harvest_running(state):
        stage_log(state, "prices: REFUSED — a harvest for this state is already running")
        return 2
    log = LOGS / f"{state}-prices.log"
    args = ["scripts/build_data.py", "--region", state, "--stage", "prices",
            "--workers", str(workers)]
    if only:
        args += ["--only", only]
    if refresh_all:
        args.append("--refresh-all")
    stage_log(state, f"prices: start (workers={workers}{', only' if only else ''})")
    t0 = time.time()
    rc = run(args, log)
    n, c = status_counts(state)
    stage_log(state, f"prices: exit={rc} in {(time.time()-t0)/3600:.1f}h — "
                     f"{n} hospitals {dict(c)}")
    return rc


def do_finish(state: str, no_publish: bool, no_commit: bool,
              ignore_coherence: bool = False) -> int:
    if harvest_running(state):
        stage_log(state, "finish: REFUSED — harvest still running")
        return 2
    n, c = status_counts(state)
    if not n:
        stage_log(state, "finish: no status file — run prices first")
        return 1
    if not c.get("ok"):
        stage_log(state, "finish: STOP — zero ok hospitals, nothing to publish")
        return 4
    no_rows, orphan = coherence(state)
    gap = catalogue_gap(state)
    stage_log(state, f"finish: coherence ok-without-rows={len(no_rows)} "
                     f"orphan-priced={len(orphan)} ok-without-catalogue={len(gap)}")
    if (no_rows or gap) and not ignore_coherence:
        ids = ",".join(sorted(set(no_rows) | set(gap)))
        (LOGS / f"{state}-reharvest-ids.txt").write_text(ids, encoding="utf-8")
        stage_log(state, f"finish: STOP — re-harvest first: "
                         f"run_state.py {state} prices --only \"$(cat logs/{state}-reharvest-ids.txt)\"")
        return 3

    log = LOGS / f"{state}-finish.log"
    stage_log(state, "merge: start")
    if run(["scripts/merge_shards.py", "--region", state], log):
        stage_log(state, "merge: FAILED")
        return 1
    site = load(DATA / f"{state}.json", {})
    stage_log(state, f"merge: {len(site.get('hospital_meta', {}))} hospitals in {state}.json, "
                     f"{len(site.get('prices', {}))} procedures")

    stage_log(state, "validate: start")
    rc = run(["scripts/validate_data.py", "--region", state], log)
    stage_log(state, f"validate: exit={rc} (plausibility FAILs on odd published prices are expected; read the log)")

    # Cross-state summary + per-state trends for the compare / movers views.
    if run(["scripts/build_compare.py"], log):
        stage_log(state, "compare: build_compare.py FAILED (non-fatal; site compare page may be stale)")
    else:
        stage_log(state, "compare: compare.json + trends rebuilt")

    # CMS Care Compare quality (ER times, safety, patient survey) for this state.
    if run(["scripts/build_quality.py", "--region", state], log):
        stage_log(state, "quality: build_quality.py FAILED (non-fatal; cards show no quality data)")
    else:
        stage_log(state, "quality: <state>-quality.json rebuilt")

    # regions.json must still carry catalogue_base for every published region.
    regions = load(DATA / "regions.json", {})
    lost = [r for r, m in regions.items() if r != state and not m.get("catalogue_base")]
    if lost:
        stage_log(state, f"merge: WARNING catalogue_base missing for {lost}")

    if no_commit:
        stage_log(state, "commit: skipped (--no-commit)")
        return 0
    git("add", "data/")
    if git("diff", "--staged", "--quiet").returncode == 0:
        stage_log(state, "commit: nothing changed")
    else:
        hosp = regions.get(state, {}).get("hospitals", "?")
        r = git("commit", "-m", f"{state}: publish {hosp} hospitals ({datetime.now():%Y-%m-%d})")
        if r.returncode:
            stage_log(state, f"commit: FAILED {r.stderr.strip()[:200]}")
            return 1
        r = git("push")
        stage_log(state, "push: " + ("ok" if r.returncode == 0 else f"FAILED {r.stderr.strip()[:200]}"))

    if no_publish:
        stage_log(state, "release: skipped (--no-publish)")
        return 0
    stage_log(state, "release: start (catalogue upload)")
    rc = run(["scripts/publish_release.py", "--region", state], LOGS / f"{state}-release.log")
    if rc:
        stage_log(state, f"release: exit={rc} — re-run `run_state.py {state} finish` later; "
                         f"it resumes from the manifest")
        return rc
    # publish_release rewrote regions.json (+ maybe release-map); commit them.
    git("add", "data/regions.json", f"data/release-map-{state}.json")
    if git("diff", "--staged", "--quiet").returncode != 0:
        git("commit", "-m", f"{state}: publish release map + catalogue base")
        r = git("push")
        stage_log(state, "release: map committed, push " + ("ok" if r.returncode == 0 else "FAILED"))
    return do_probe(state)


def do_probe(state: str) -> int:
    """Live wiring: deployed regions.json has the region + base; search.json is reachable."""
    stage_log(state, "probe: waiting for Pages (up to 6 min)")
    base = None
    for _ in range(12):
        try:
            req = urllib.request.Request(f"{SITE}/data/regions.json?x={int(time.time())}",
                                         headers={"User-Agent": "care-cost-explorer-probe"})
            with urllib.request.urlopen(req, timeout=30) as r:
                live = json.loads(r.read().decode("utf-8"))
            if state in live and live[state].get("catalogue_base"):
                base = live[state]["catalogue_base"]
                break
        except Exception:
            pass
        time.sleep(30)
    if not base:
        stage_log(state, "probe: FAILED — deployed regions.json lacks the region or its catalogue_base")
        return 1
    codes = [probe(f"{SITE}/data/{state}.json"),
             probe(f"{SITE}/data/{state}-hospitals.json"),
             probe(f"{base}/{state}--search.json")]
    stage_log(state, f"probe: {state}.json={codes[0]} hospitals={codes[1]} search={codes[2]} base={base}")
    return 0 if all(c == 200 for c in codes) else 1


def do_status(state: str) -> int:
    n, c = status_counts(state)
    print(f"{state}: {n} hospitals {dict(c)}")
    no_rows, orphan = coherence(state)
    print(f"  ok-without-rows={len(no_rows)} orphan-priced={len(orphan)} "
          f"ok-without-catalogue={len(catalogue_gap(state))} harvest_running={harvest_running(state)}")
    st = load(DATA / f"{state}-status.json", {})
    cls = Counter((v.get("status"), (v.get("detail") or "")[:60]) for v in st.values()
                  if v.get("status") not in ("ok", "exempt"))
    for (s, d), k in cls.most_common(12):
        print(f"  {k:4d} {s:12s} {d}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("state")
    ap.add_argument("stage", choices=["registry", "prices", "finish", "probe", "status"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--refresh-all", action="store_true")
    ap.add_argument("--no-publish", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--ignore-coherence", action="store_true",
                    help="publish even if some ok hospitals lack rows/catalogue (first-pass program)")
    a = ap.parse_args()
    state = a.state.lower()
    if not state.replace("-", "").isalnum():
        print("bad state code"); return 1
    os.chdir(ROOT)
    if a.stage == "registry":
        return do_registry(state)
    if a.stage == "prices":
        return do_prices(state, a.workers, a.only, a.refresh_all)
    if a.stage == "finish":
        return do_finish(state, a.no_publish, a.no_commit, a.ignore_coherence)
    if a.stage == "probe":
        return do_probe(state)
    return do_status(state)


if __name__ == "__main__":
    sys.exit(main())
