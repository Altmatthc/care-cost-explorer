# Licensing

This project is **source-available, non-commercial**. The code is public and
readable; using it to make money requires permission from the copyright holder.

| What | Licence | Effect |
|---|---|---|
| Code (`scripts/`, `index.html`, workflows) | **PolyForm Noncommercial 1.0.0** | Use, modify and share freely for any non-commercial purpose. Commercial use requires a separate licence. |
| Collected data (`data/`) | **CC BY-NC-SA 4.0** | Attribution, non-commercial, share-alike |

Both reserve commercial rights to the copyright holder, who remains free to
grant commercial licences on any terms — including to themselves.

---

## What "non-commercial" permits

Allowed, no permission needed:

- Personal use, by anyone
- Research, teaching, journalism and academic work
- Use by charities and non-profits for their charitable purposes
- Government use
- Reading, forking, modifying and publishing modified versions, so long as the
  use stays non-commercial

Not allowed without a separate licence:

- Running it as a paid or ad-supported service
- Incorporating it into a commercial product
- Internal use by a for-profit company as part of its business

PolyForm defines the line as any purpose other than one "intended for or
directed toward commercial advantage or monetary compensation." Deliberately
broad, and like all such wording it has grey areas — see below.

---

## What this actually protects, honestly

**Protected:**

- The code. Commercial use without a licence is a breach.
- The specific expression — structure, wording and layout of these files.

**Not protected, and no licence can change this:**

- **The idea.** Comparison shopping for hospital care, a body-map picker,
  ranking by price and distance — concepts are not copyrightable. Anyone may
  build something similar from scratch and sell it.
- **The prices.** US copyright does not protect facts
  (*Feist Publications v. Rural Telephone Service*, 499 U.S. 340 (1991)).
  Every price here comes from a file a hospital must publish by federal
  mandate. Anyone can download the same files and extract the same numbers.
- **The frontend.** Every visitor's browser downloads the HTML, CSS and
  JavaScript to render the page. Unavoidable for any website, under any
  licence or repository setting.

---

## Trade-offs you are accepting

Stated plainly, because they are real:

**This is not open source.** PolyForm Noncommercial is not OSI-approved.
Expect fewer outside contributors, exclusion from some directories and package
ecosystems, and organisations whose policies forbid non-OSI dependencies
declining to touch it.

**"Commercial" is genuinely ambiguous at the edges.** A hospital's own pricing
team? A non-profit insurer? A journalist at a for-profit newspaper? A
researcher on industry funding? You will be answering these questions, and the
answers are yours to give — you can always grant permission.

**Enforcement means litigation.** A licence is only as strong as your
willingness to act on a breach, and that costs money you would rather spend on
coverage.

**Relicensing gets harder once others contribute.** As sole copyright holder
you can change the licence whenever you like. Once someone else's code is
merged you need their agreement too. If you expect contributions and want to
keep that freedom, ask contributors to agree that you may relicense their work.

If any of this becomes a problem, AGPL-3.0 is the natural fallback: it keeps
the work open and still makes a closed-source competitor impossible, while
permitting commercial use that publishes its changes.

---

## What actually protects this project

Not the licence. Three other things:

1. **The price history.** `data/*-history.csv` records what prices *were*.
   Hospitals overwrite their files in place and no public archive of past
   versions exists anywhere. A competitor starting today cannot obtain what
   this project recorded last quarter — not for money, not at all. It is the
   only genuinely non-replicable asset here, and it grows every month the
   pipeline runs.

2. **The accumulated corrections.** That Kaiser names its San Diego file after
   the Zion campus, that Sharp publishes six files, that CMS lists Grossmont
   without "Sharp" in the name, that a hospital's legal attestation text will
   fool a naive header parser — none of this is in any specification. It was
   earned by failing repeatedly.

3. **Being correct.** Anyone can scrape these files. Publishing numbers that
   survive scrutiny is the hard part.

---

## Commercial licensing

Commercial use is available by agreement. Open an issue or contact the
repository owner.

## Attribution

> Care Cost Explorer — hospital price transparency data compiled from files
> published under 45 CFR Part 180.
> https://github.com/Altmatthc/care-cost-explorer

## Third-party components

These carry their own licences and are **not** restricted by the terms above:

- **Leaflet** (map library) — BSD-2-Clause
- **Map tiles** — © OpenStreetMap contributors, © CARTO
- **Hospital registry** — CMS Provider Data Catalog, US public domain
- **Geocoding** — US Census Bureau, public domain
- **ZIP lookup** — Zippopotam.us
- **Price files** — published by each hospital under federal requirement

Nothing here restricts anyone's right to obtain those sources directly.

CPT® codes are copyrighted by the American Medical Association. This project
stores only code *numbers* as they appear in hospitals' own public files, not
the AMA's descriptors. Displaying official CPT descriptions would require a
licence from the AMA.
