# Quality Rules

All rules are applied automatically. None can be disabled at runtime.

## Phone validation

- Parsed by `libphonenumber` → must produce a valid E.164 number
- Parsed region must match expected ISO2. Mismatch → confidence -20
- Country prefix whitelist: only numbers starting with the country's dialling code accepted
- Numbers appearing across 2+ unrelated companies in the same run are flagged as intermediary numbers and blanked out

## Email validation

- Format check (regex + `email-validator`)
- MX record lookup via `dnspython`
- Optional SMTP RCPT probe (`--smtp-probe`)
- Free providers (gmail, yahoo, outlook, etc.) capped at confidence 55
- Recruitment / HR emails (recrutement@, jobs@, rh@, careers@) flagged with -10 penalty
- Emails appearing across 2+ companies in the same run are blanked out

## Domain verification (Fix 1)

For business emails (non-free-provider), the domain is fetched over HTTP:

1. TLD check: if the TLD is a non-target-country ccTLD (e.g. `.fr` for a BF company) → rejected (-15)
2. Page fetch: `/`, `/contact`, `/about` etc.
3. Country signal check: page text must contain phone codes, city names, or country keywords for the target ISO2
4. Company name fuzzy match: company name must appear in the page text (partial_ratio ≥ 60)

If fetch fails → `unverified` (-10, confidence capped at 45).
If verified → `accepted` (+10, waives collision penalty).

## Parent company detection (Fix 5/15)

URL-pattern check (no HTTP):

- If `iso2.lower()` is NOT in the domain, AND
- The company's primary identifier token IS in the domain, AND
- NOT guarded by ≥2 significant tokens all present in the domain (own domain guard)

→ flagged as `possible_parent_company` (-20)
→ triggers a Serper subsidiary search: `"{company_name}" {country} -site:{parent_domain}`

## Directory listing validation (Fix 10)

For Go Africa Online and other directory URLs found via Serper:

- Extract slug from URL (strip leading ID and trailing location tokens)
- Fuzzy match (token_set_ratio) against company name
- Page title also checked
- Both < 70 → rejected as wrong-company listing
- One < 70 → accepted with `directory_slug_ambiguous_match` note (-15 penalty)

## News URL rejection (Fix 8)

URLs whose path contains article/press segments are rejected as company websites:

Path segments: `actualite`, `actualites`, `news`, `article`, `presse`, `communique`, `blog`, `reportage`, `portrait`, `success-story`

Path prefixes: `/retour-de-`, `/interview-`

## Facebook country conflict (Fix 16)

If the Facebook URL slug contains words implying a different country than the searched company (e.g. `france`, `senegal`, `ghana` for a BF company), the result is skipped.

## Collision risk (Fix 6)

Companies with highly generic names (single common word, sector term only, etc.) get a `high` collision risk flag:

- Confidence capped at 75
- Note: "High name collision risk — verify manually"

Both penalties waived if domain verification returns `accepted`.

## Confidence formula

```
base = average(individual phone/email confidences)
     + 10  if any phone confirmed by ≥2 independent sources
     - 5   per phone from a single unconfirmed source
     + 5   if any email has MX/SMTP validation
     - 15  if fuzzy name match < 80
     - 20  if phone country mismatch
     - 10  if free-provider email
     - 15  if directory slug ambiguous match
     - 20  if possible parent company
     - (email_penalty from domain verification)
     - (role_penalty from recruitment email flag)
     - (website_penalty from directory/parent detection)
```

Clipped to [0, 100].

## Status thresholds

| Confidence | Status |
|---|---|
| ≥ 65, has phone + (email or website) | `enriched` |
| ≥ 50, any data | `partial` |
| 35–49 | `ambiguous` |
| < 35 | `low_confidence` |
| 0 data | `not_found` |

Recruitment-only emails force status down to `partial` regardless of confidence.
