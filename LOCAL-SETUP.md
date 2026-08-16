# Running locally

Running on your own machine removes GitHub's 6-hour job limit, so a whole
state can run in one go without sharding. It's also far faster to debug: the
test suite takes 30 seconds instead of a workflow round-trip.

GitHub Actions stays useful for scheduled refreshes. Local is better for the
big initial pulls and for anything you're troubleshooting.

---

## One-time setup

### 1. Get the code onto your machine

**With GitHub Desktop** (easiest, no terminal):
[desktop.github.com](https://desktop.github.com) → sign in → **Clone a
repository** → pick `care-cost-explorer` → note the folder it chose.

**With git:**
```
git clone https://github.com/Altmatthc/care-cost-explorer.git
cd care-cost-explorer
```

### 2. Check you have Python

**macOS** — open Terminal (Cmd+Space, type "Terminal"):
```
python3 --version
```
If it's missing or below 3.9, install from
[python.org/downloads](https://www.python.org/downloads/).

**Windows** — open PowerShell (Start menu, type "PowerShell"):
```
python --version
```
If nothing happens, install from
[python.org/downloads](https://www.python.org/downloads/) and **tick "Add
Python to PATH"** during install. That checkbox is easy to miss and everything
fails without it.

### 3. Run the setup script

From inside the project folder:

**macOS:**
```
python3 scripts/setup_local.py
```

**Windows:**
```
python scripts\setup_local.py
```

It creates an isolated environment in `.venv`, installs the two dependencies,
and runs the test suite to prove it works. Safe to run again any time.

---

## Everyday use

Everything goes through the runner, which finds the environment for itself —
nothing to activate. Use `python3` on macOS, `python` on Windows.

```
python3 scripts/run.py test              # 30-second self-check, no network
python3 scripts/run.py refresh           # update San Diego
python3 scripts/run.py check             # validate the published prices
python3 scripts/run.py trends            # price movements over time
python3 scripts/run.py serve             # preview the site locally
python3 scripts/run.py help              # everything else
```

Each command prints the underlying `build_data.py` invocation before running,
so you can see exactly what it does and run it by hand when you'd rather.

### Preview before publishing

```
python3 scripts/run.py serve
```

Then open **http://localhost:8000**. This serves your local `data/` files, so
you see exactly what visitors would see — before anything is committed.

`file://` won't work: the browser blocks the data fetches. Use the server.

---

## Pulling a whole state

```
python3 scripts/run.py region ca
```

Roughly 400 hospitals and several gigabytes. Expect hours. Three things make
that safe:

- **It checkpoints after every hospital.** Stop it with Ctrl+C and re-run to
  pick up where it left off — finished hospitals are skipped.
- **It's polite.** Four concurrent downloads, one per host, with a pause
  between requests to the same server.
- **It's resumable across days.** A hospital collected successfully isn't
  re-downloaded for 30 days, and unchanged files are detected without
  downloading at all.

Raise concurrency if your connection can take it:
```
python3 scripts/build_data.py --region ca --stage prices --workers 8
```

Still one request per host at a time regardless — that limit is deliberate.

---

## Publishing what you collected

Local runs change files in `data/` but publish nothing. To put it live:

**GitHub Desktop:** the changed files appear automatically → write a summary →
**Commit to main** → **Push origin**.

**git:**
```
git add data/
git commit -m "Refresh California prices"
git push
```

The site updates within a minute or two.

---

## When something looks wrong

```
python3 scripts/run.py check                     # flags implausible prices
python3 scripts/run.py probe <url>               # inspect a file's structure
python3 scripts/run.py verify <url> 44970        # raw rows for one code
python3 scripts/run.py only kaiser               # re-scan just some hospitals
```

`verify` is the one that settles arguments — it prints the hospital's actual
rows for a billing code so you can confirm a displayed price is really what
the file says.

---

## Troubleshooting

**"python: command not found" (macOS)** — use `python3`.

**"python is not recognized" (Windows)** — Python isn't on PATH. Reinstall
with "Add Python to PATH" ticked.

**"externally-managed-environment" from pip** — this is why setup uses a
`.venv`. Run `setup_local.py` rather than installing packages globally.

**Permission errors on macOS** — you're probably outside your home folder.
Clone somewhere like `~/Documents`.

**A run stops partway** — that's fine, it checkpointed. Re-run the same
command.

**Slow, or a server refuses you** — hosts that refuse connections go on a
one-day cooldown automatically and are skipped. Their existing data is kept.

---

## Keeping .venv out of the repository

`.venv` holds hundreds of files and should never be committed. Check that
`.gitignore` contains:

```
.venv/
__pycache__/
*.pyc
```
