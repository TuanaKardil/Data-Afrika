# Setup Guide

## Requirements

- Python 3.11+
- A Serper.dev API key (free: 2,500 searches/month)
- An Anthropic API key (for AI judge on ambiguous cases)

## Installation

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/afrienrich.git
cd afrienrich

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install
pip install -e ".[dev]"
```

## Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
SERPER_API_KEY=your_serper_key
ANTHROPIC_API_KEY=your_anthropic_key
```

## First run

```bash
# Test with a small sample (no live API calls)
afrienrich enrich your_file.xlsx --max-rows 5 --dry-run

# Real run (5 rows, no cache)
afrienrich enrich your_file.xlsx --max-rows 5 --no-cache

# Full run
afrienrich enrich your_file.xlsx
```

Output is saved to `~/Desktop/your_file.enriched.xlsx`.

## Input file format

Minimum required columns in the `.xlsx`:

| Column | Example |
|---|---|
| `importer_name` | SONG NABA DISTRIBUTION |
| `country` | BF |

Optional: `hs_code`, `category_hint`, `cleaned_name`. Any extra columns are carried through to the output unchanged.

## Resuming an interrupted run

If a run crashes, just re-run the same command. It reads the `.staging.csv` file and skips already-processed rows:

```bash
afrienrich enrich your_file.xlsx  # resumes automatically
```

To start fresh:

```bash
afrienrich enrich your_file.xlsx --no-resume
```

## Stats and verification

```bash
afrienrich stats your_file.enriched.xlsx
afrienrich verify your_file.enriched.xlsx
```

## Serper credit usage

Each company uses approximately:
- 1 credit for web search
- 1 credit for Maps/Places
- 1 credit for GoAfrica discovery
- 1 credit for Facebook discovery

~4 credits per company. Free tier (2,500/month) = ~600 companies/month.
