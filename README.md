# Care Cost Explorer

A hospital price comparison site built on the price files US hospitals are
federally required to publish (45 CFR Part 180). Pick a body area, pick a
procedure, enter your ZIP, and see what nearby hospitals charge.

**Everything below can be done from an iPad in a browser. No terminal, ever.**

---

## What this repo contains

| File | What it does |
|---|---|
| `index.html` | The website. Runs standalone — works with placeholder data before any pipeline run. |
| `scripts/build_data.py` | Pulls the hospital registry from CMS, geocodes it, downloads and parses price files. |
| `scripts/merge_shards.py` | Merges parallel job output and normalizes payer names. |
| `.github/workflows/update-data.yml` | Runs the pipeline on GitHub's servers, monthly or on demand. |
| `data/` | Generated output. Empty until the first run. |

---

## Part 1 — Put it online (about 10 minutes)

### Step 1: Create the repository
1. Go to [github.com/new](https://github.com/new) and sign in (create a free account if needed).
2. Name it `care-cost-explorer`. Choose **Public**. Click **Create repository**.

### Step 2: Upload these files
1. On the new repo page, click **uploading an existing file**.
2. Upload every file from this bundle, keeping the folder structure.
   On iPad, use **Files → select all → Upload**. If the folder structure
   doesn't survive, create the folders manually with the **Add file →
   Create new file** button by typing `scripts/build_data.py` as the
   filename — typing a `/` creates the folder.
3. Click **Commit changes**.

### Step 3: Turn on the website
1. In your repo, click **Settings** (top bar).
2. Click **Pages** in the left sidebar.
3. Under **Source**, choose **Deploy from a branch**.
4. Branch: **main**, folder: **/ (root)**. Click **Save**.
5. Wait ~2 minutes. Your site is live at
   `https://YOUR-USERNAME.github.io/care-cost-explorer/`

At this point the site works, using placeholder prices. The banner says so.

---

## Part 2 — Load real data (one click, then wait)

### Step 4: Allow the pipeline to save its results
1. **Settings → Actions → General** (left sidebar).
2. Scroll to **Workflow permissions**.
3. Select **Read and write permissions**. Click **Save**.

This lets the job commit the data it collects back into the repo.

### Step 5: Run it
1. Click the **Actions** tab at the top of your repo.
2. If prompted, click **I understand my workflows, go ahead and enable them**.
3. Click **Update hospital price data** in the left sidebar.
4. Click **Run workflow** (right side), leave region as `san-diego`, click the
   green **Run workflow** button.

Watch it run by clicking into the job. San Diego takes roughly 20–60 minutes,
mostly spent downloading large files.

When it finishes, reload your site. The banner should turn green and read
**"Live data."** If it still says placeholder, see Troubleshooting.

From then on it refreshes itself on the 1st of each month.

---

## Part 3 — Expanding coverage

Scope is one dropdown. In **Actions → Update hospital price data → Run
workflow**, change **region**:

| Region | Hospitals | Realistic runtime | Shards to use |
|---|---|---|---|
| `san-diego` | ~20 | 20–60 min | 1 |
| `california` | ~400 | 4–10 hours | 8–12 |
| `us` | ~5,400 | see below | not in one run |

**Set `shards` higher for bigger regions.** Shards split the work across
parallel jobs. California with 10 shards runs ten hospitals' files at once
instead of one at a time.

### An honest warning about nationwide

Going national is not simply a bigger version of the same job. Price files
routinely run 50MB to over 1GB **each**. A full US refresh means downloading
several terabytes. That does not fit in GitHub's free tier — jobs are capped
at 6 hours and storage is limited.

Three realistic paths when you get there:

1. **Rotate by state.** Refresh a few states per week rather than everything
   at once. The workflow already supports this — add a state region and
   schedule them on different days. This is the cheapest option and the one
   to start with.
2. **Rent a server.** A modest cloud VM with real bandwidth and disk (roughly
   $20–80/month) can handle a rolling national refresh.
3. **License parsed data.** Companies like Turquoise Health and
   PatientRightsAdvocate have already parsed every hospital's files. Paying
   for their dataset skips the hardest engineering problem entirely.

To add a state, edit `REGIONS` in `scripts/build_data.py` (you can edit files
directly on github.com — click the file, then the pencil icon):

```python
"texas": {"label": "Texas", "state": "TX", "counties": None},
```

Then add `texas` to the `options` list in `.github/workflows/update-data.yml`.

---

## Where the data comes from

Everything is public and free. No API keys anywhere in this repo.

- **Hospital registry** — CMS Provider Data Catalog, dataset `xubh-q36u`
  ("Hospital General Information"). All ~5,400 Medicare-registered US
  hospitals with addresses, type, ownership, emergency-services flag, and
  CMS overall star rating.
- **Coordinates** — US Census Bureau geocoder.
- **Prices and insurance networks** — each hospital's own machine-readable
  file, located via the `/cms-hpt.txt` file CMS has required in every
  hospital's website root since the CY2024 rule. The file lists negotiated
  rates per payer, which is where the insurance dropdown gets its data.

---

## Troubleshooting

**Banner still says "placeholder" after a successful run.**
Check that `data/san-diego.json` exists in your repo. If not, the publish step
likely couldn't commit — revisit Step 4 (workflow permissions).

**Many hospitals show "no price file found."**
Expected, unfortunately. Compliance is genuinely incomplete, and some systems
put prices behind interactive portals rather than files. The GAO and CMS have
both documented this. Hospitals that fail discovery can be fixed by hand:
open `data/san-diego-hospitals.json` on github.com, find the hospital, and
paste the file's URL into its `mrf_url` field.

**A hospital's file downloads but yields zero rows.**
Its file is probably JSON rather than CSV, which needs a per-schema parser
(noted as a limitation in `build_data.py`). Or it uses column names the
loose matcher misses — the job log prints which columns it found.

**The workflow times out.**
Raise the shard count and re-run.

---

## Important limitations

- **Published prices are not quotes.** They're what hospitals report. Your
  actual bill depends on your deductible, coinsurance, and the care you
  actually receive — and physician fees are usually billed separately and are
  *not* in these files.
- **ER wait times and some hospital details are still placeholders.** The real
  source is CMS Care Compare timely-care measures; wiring that in is a natural
  next addition.
- **Coverage is uneven by design of the underlying data**, not by choice here.
- This is an information tool. It is not medical advice, and it should never
  be a reason to delay emergency care.

---

## License

Code: MIT. Underlying government data is public domain. CPT codes are
copyrighted by the American Medical Association; this project stores only
code numbers as published in hospitals' own public files.
