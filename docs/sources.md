# Data Sources

AfriEnrich runs all sources for every company and merges results by confidence.

## Source Overview

| Source | Tier | Base Confidence | Rate Limit |
|---|---|---|---|
| serper_web | 1 | 65 | 2/sec |
| go_africa_online | 2 | 75 | 0.5/sec |
| facebook | 2 | 65 | 0.5/sec |
| serper_maps | 3 | 70 | 2/sec |
| website_crawler | 3 | 70 | 0.33/sec |
| email_prober | 5 | 55 | 0.5/sec |

## serper_web

Queries Google SERP via Serper.dev in the company's primary language.

Query template: `{name} contact téléphone email {country}`

Extracts phones, emails, website URLs, and social URLs from snippets and organic results.

## go_africa_online

The main francophone African business directory. High yield for BF, CI, SN, TG, ML, NE, GN.

**Discovery**: uses Serper with `site:goafricaonline.com/{iso2} "{company_name}"` to find the listing URL. Fetches and parses the listing page directly.

**Validation (Fix 10)**: the URL slug and page title are both fuzzy-matched against the queried name. If both score < 70, the listing is rejected. If one passes and the other fails, the result is accepted with a `-15` confidence penalty (`directory_slug_ambiguous_match`).

## facebook

Finds the company Facebook page via 5 Serper query variations, then fetches the page for contact extraction.

**Country conflict detection (Fix 16)**: if the Facebook URL slug contains a word implying a different country (e.g. `decomatfrance` for a BF company), the result is skipped.

**Personal profiles**: accepted at a lower threshold (score ≥ 55) with a `facebook_type: personal_profile` note.

## serper_maps

Queries Serper's Google Maps / Places endpoint. Returns structured data: phone, website, address, GPS coordinates.

## website_crawler

Crawls the company's discovered website. Tries these paths in order:
`/`, `/contact`, `/contacts`, `/contactez-nous`, `/contact-us`, `/nous-contacter`, `/about`, `/about-us`, `/a-propos`, `/quem-somos`, `/contato`, `/home`, `/accueil`, `/secretariat`, `/direction`

Extracts phones, emails, social URLs, address, and GPS from full page text.

## email_prober

Probes pattern addresses on the discovered domain: `info@`, `contact@`, `ventes@`, `sales@`, `direction@`, etc. Guarded by MX check. Disabled by default — enable with `--sources email_prober`.

---

## Adding a new source

1. Create `src/afrienrich/sources/my_source.py`
2. Implement `BaseSource`:

```python
from .base import BaseSource
from ..models import CompanyQuery, SourceResult

class MySource(BaseSource):
    name = "my_source"
    tier = 4
    base_confidence = 60
    rate_limit_per_second = 0.5

    async def search(self, company: CompanyQuery) -> SourceResult:
        ...
        return SourceResult(source_name=self.name, phones=[], emails=[], ...)
```

3. Register it in `pipeline.py` → `_build_sources()`
4. Add rate limit to `config/sources.yaml`
