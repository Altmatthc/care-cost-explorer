# Scaling to California

San Diego is 19 hospitals. California is roughly 400. This is what changes,
and the order to do it in.

## Before you start

**Disk.** Allow ~20 GB free. The price files themselves are the bulk —
several are over 3 million rows, and a few systems publish files above 1 GB.
They're streamed, not stored, but the catalogue and working files add up.

**Time.** Expect 4–8 hours with `--workers 8`. It checkpoints after every
hospital, so Ctrl+C and re-run is always safe and always resumes.

**Don't run it on battery, and disable sleep.** A laptop suspending
mid-download will drop connections; the run survives it, but you'll waste
time. On Windows: Settings → System → Power → Screen and sleep → set both to
Never while it runs.

---

## Step 1 — move the catalogue to Releases first

Do this *before* California, not after. Committing a statewide catalogue to
git is roughly 100 MB per refresh, permanently.

```
winget install --id GitHub.cli      # once
gh auth login                       # once
python scripts/run.py release       # publishes San Diego's catalogue
```

Check what it would do first if you like:

```
python scripts/publish_release.py --region san-diego --dry-run
```

It records the release URL in `regions.json`, which is how the site knows to
look there. Only changed files upload, so later refreshes are quick.

Then stop committing the catalogue — add to `.gitignore`:

```
data/*-search.json
data/*-codes/
data/*-catalogue/
```

Commit `regions.json` and `.gitignore`, push, and confirm code search still
works on the live site. **Verify this with San Diego before running
California** — debugging a release URL problem is far easier against 1,180
files than 30,000.

---

## Step 2 — run California

```
python scripts/run.py region ca
```

That runs registry, geocode, prices at 8 workers, merge, and validate.

The geocode stage will take a while on its own: ~400 addresses, one request
at a time, with a pause between each to stay polite to the Census API.

---

## Step 3 — publish

```
python scripts/run.py check
python scripts/run.py release ca
git add .
git commit -m "California"
git push
```

`run.py check` matters more at this scale than it did for San Diego. With 400
hospitals you cannot eyeball the output; the validator is how you find the
$2 blood tests.

---

## What will go wrong, and what to do

**Some hospitals will refuse connections.** They go on a one-day cooldown
automatically and are skipped. Run again tomorrow and they'll be picked up.

**Some will publish no discoverable file.** Expect roughly 15–25% — that
matches what San Diego showed, and the national compliance picture. They're
recorded with a status and shown on the site as gaps, which is the honest
outcome.

**A few will be very slow.** Files above 1 GB exist. The 300-second timeout
plus retries usually gets there.

**The run may take more than one sitting.** That's fine and expected. Stop
it, run it again later, and it continues — hospitals already collected are
skipped for 30 days.

---

## After California

`python scripts/run.py size` shows where the repository stands.

For further states, the same three commands work with any state code:

```
python scripts/run.py region tx
python scripts/run.py release tx
```

Nationwide is ~5,400 hospitals — roughly 14× California. That is a rotation
across many sittings, not one run. Doing a few states at a time keeps each
session manageable and each dataset verifiable.
