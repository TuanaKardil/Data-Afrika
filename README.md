# AfriEnrich

CLI tool that takes an Excel list of African importer companies and enriches it with phone numbers, emails, websites, and social media profiles. Every data point is source-labelled and confidence-scored.

---

## What it does

For each company in your input file, AfriEnrich:

1. Searches Google (via Serper.dev) for contact information
2. Searches Google Maps/Places for the business listing
3. Looks up the company on Go Africa Online (the main African business directory)
4. Finds and crawls the company's own website (`/contact`, `/about`, etc.)
5. Finds the company's Facebook page and extracts contact info
6. Deduplicates and scores all collected data
7. Writes a clean Excel output with up to **8 phones** and **5 emails** per company, each labelled with its source and confidence score

Output columns include: `Telefon 1–8`, `E-posta 1–5`, `Website`, `Facebook`, `LinkedIn`, `Durum` (status), `Güven (%)` (confidence).

---

## Quickstart

### 1. Install

```bash
cd afrienrich
pip install -e ".[dev]"
```

### 2. Set up API keys

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Required:
- `SERPER_API_KEY` — from [serper.dev](https://serper.dev) (free tier: 2,500 searches/month)
- `ANTHROPIC_API_KEY` — from [console.anthropic.com](https://console.anthropic.com) (used for AI judge on ambiguous cases)

### 3. Run

```bash
afrienrich enrich companies.xlsx
```

Output is saved to `~/Desktop/companies.enriched.xlsx` by default.

---

## Input format

An `.xlsx` file with at least these columns:

| Column | Required | Description |
|---|---|---|
| `importer_name` | ✅ | Company name |
| `country` | ✅ | ISO2 country code (e.g. `BF`, `CI`, `SN`, `GH`, `NG`) |
| `hs_code` | optional | HS trade code |
| `category_hint` | optional | Sector hint |

Any extra columns are preserved verbatim in the output.

---

## Output format

Four sheets:

| Sheet | Contents |
|---|---|
| **Sonuçlar** | Main results — one row per company, all contact data |
| **Audit** | Every source query with URL, status, and latency |
| **Kaynak İstatistik** | Per-source hit rate, latency, block rate |
| **Hatalar** | Any unhandled errors with row context |

**Status values** (`Durum` column):

| Status | Meaning |
|---|---|
| `enriched` | Confidence ≥ 65, has phone + email or website |
| `partial` | Confidence ≥ 50, some data found |
| `ambiguous` | Confidence 35–49, conflicting signals |
| `low_confidence` | Confidence < 35, data found but unreliable |
| `not_found` | No data found at any source |

---

## CLI options

```
afrienrich enrich INPUT.xlsx [OPTIONS]

  -o, --output PATH         Output path (default: ~/Desktop/INPUT.enriched.xlsx)
  -c, --country ISO2        Override country for all rows
  --resume / --no-resume    Resume from partial run (default: resume)
  --retry-not-found         Re-process rows previously marked not_found
  --smtp-probe              Enable SMTP email probing (slower but more accurate)
  --no-cache                Bypass cache (re-fetch everything)
  --high-quality            Use Claude Opus for AI judge (higher cost)
  --max-rows N              Process only first N rows (for testing)
  --concurrency N           Parallel rows (default: 5)
  --sources LIST            Comma-separated source whitelist
  --exclude-sources LIST    Comma-separated source blacklist
  --dry-run                 Validate input only, do not call any sources

afrienrich verify OUTPUT.xlsx    # Re-validate phones and emails
afrienrich stats OUTPUT.xlsx     # Print enrichment statistics
```

---

## Sources

All sources run for every company. Results are merged and sorted by confidence.

| Source | What it does | Tier |
|---|---|---|
| `serper_web` | Google search in country's primary language | 1 |
| `go_africa_online` | Searches Go Africa Online directory via Serper `site:` operator | 2 |
| `facebook` | Finds Facebook page via Serper, fetches contact info | 2 |
| `serper_maps` | Google Maps / Places API | 3 |
| `website_crawler` | Crawls company website: `/`, `/contact`, `/about`, `/secretariat`, `/direction` + more | 3 |
| `email_prober` | Probes `info@`, `contact@`, `ventes@` etc. on discovered domain (opt-in via `--sources email_prober`) | 5 |

---

## Quality rules

- **Phone validation**: every phone is parsed by `libphonenumber` and checked against the expected country code. Wrong-country numbers are rejected.
- **Email validation**: format check → MX lookup → optional SMTP probe.
- **Domain verification**: for business emails, the domain is fetched and checked for country signals and company name match.
- **Parent company detection**: if the discovered website domain contains the company's primary identifier but NOT the country ISO2, it is flagged as a possible parent company and a subsidiary search is run.
- **Duplicate detection**: phone/email numbers that appear across 2+ unrelated companies in the same run are flagged as probable intermediary/broker numbers and blanked out.
- **Directory slug matching**: Go Africa Online and similar directory results are fuzzy-matched against the queried company name. Score < 70 → rejected.
- **News URL rejection**: URLs whose path contains article/news segments (`/actualite`, `/retour-de-`, etc.) are rejected as company websites.

---

## Project structure

```
afrienrich/
  src/afrienrich/
    main.py              # CLI entry (typer)
    pipeline.py          # Per-row orchestrator
    models.py            # Pydantic models
    normalize.py         # Name normalisation, suffix stripping
    cache.py             # SQLite cache (30-day TTL)
    sources/             # One class per data source
    extractors/          # Phone, email, website, social, address
    validators/          # Phone, email, domain, duplicate detection
    scoring/             # Confidence formula
    ai/                  # Claude judge for ambiguous cases
    excel/               # Reader + resumable writer
    utils/               # Rate limiter, fuzzy match, logging
  config/
    countries.yaml       # Per-country: phone codes, languages, registries
    sources.yaml         # Rate limits and tier per source
    email_patterns.yaml  # Patterns to probe on discovered domains
    sectors.yaml         # HS code → sector keywords
  data/
    cache.db             # SQLite cache (auto-created)
    output/              # Default output directory
    logs/                # Structured JSON logs per run
  tests/
    unit/
    integration/
    fixtures/
```

---

## Supported countries

Seed countries with full config: **BF, CI, SN, GH, NG, KE, TG, ML, NE, GN, MA, CM**

Any African country works — unknown countries fall back to English queries and generic validation. Add a country block to `config/countries.yaml` for better results.

---

## Cache

Results are cached in `data/cache.db` for 30 days. Use `--no-cache` to force a fresh fetch. The cache key is `sha256(source + normalised_company_name)`.

---

## Resume

If a run crashes or is interrupted, re-run the same command — it will skip already-processed rows (tracked in a `.staging.csv` file). Use `--no-resume` to start fresh.
