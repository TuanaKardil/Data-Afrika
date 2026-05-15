"""Prompt templates for the AI judge: disambiguate, extract_contact_from_prose, translate_name."""

from __future__ import annotations

SYSTEM_PROMPT = """You are an expert data analyst specializing in African business data enrichment.
You help validate and extract contact information (phone numbers, emails, websites) for African companies.
Your task is to analyze search results and company data and return structured JSON responses.
Always respond with valid JSON only — no markdown, no explanation, just the JSON object."""

DISAMBIGUATE_TEMPLATE = """You are given two company name candidates found for a query. Decide which, if any, is the correct match.

Query company name: {queried_name}
Country: {country}

Candidate A:
  Name: {candidate_a_name}
  Source: {candidate_a_source}
  Phone: {candidate_a_phone}
  Email: {candidate_a_email}
  Website: {candidate_a_website}

Candidate B:
  Name: {candidate_b_name}
  Source: {candidate_b_source}
  Phone: {candidate_b_phone}
  Email: {candidate_b_email}
  Website: {candidate_b_website}

Return JSON:
{{
  "choice": "A" | "B" | "none",
  "confidence": 0-100,
  "reasoning": "brief explanation"
}}"""

EXTRACT_CONTACT_TEMPLATE = """Extract all contact information from the following text for the company: {company_name} ({country}).

Text:
{text}

Return JSON:
{{
  "phones": ["list of phone numbers as found in text"],
  "emails": ["list of email addresses"],
  "website": "primary website URL or null",
  "address": "street address or null",
  "reasoning": "brief explanation of what you found"
}}

Only include items that are clearly contact details for {company_name}. Do not invent data."""

TRANSLATE_NAME_TEMPLATE = """The following company name may be in French, Portuguese, or a local African language.
Provide the best English transliteration or translation for use in an English-language web search.

Company name: {company_name}
Country: {country}
Known language: {language}

Return JSON:
{{
  "translated_name": "English version of the name",
  "transliteration": "phonetic spelling if different from translation",
  "confidence": 0-100
}}"""


def build_disambiguate_prompt(
    queried_name: str,
    country: str,
    candidate_a: dict[str, str],
    candidate_b: dict[str, str],
) -> str:
    return DISAMBIGUATE_TEMPLATE.format(
        queried_name=queried_name,
        country=country,
        candidate_a_name=candidate_a.get("name", ""),
        candidate_a_source=candidate_a.get("source", ""),
        candidate_a_phone=candidate_a.get("phone", ""),
        candidate_a_email=candidate_a.get("email", ""),
        candidate_a_website=candidate_a.get("website", ""),
        candidate_b_name=candidate_b.get("name", ""),
        candidate_b_source=candidate_b.get("source", ""),
        candidate_b_phone=candidate_b.get("phone", ""),
        candidate_b_email=candidate_b.get("email", ""),
        candidate_b_website=candidate_b.get("website", ""),
    )


def build_extract_contact_prompt(company_name: str, country: str, text: str) -> str:
    return EXTRACT_CONTACT_TEMPLATE.format(
        company_name=company_name,
        country=country,
        text=text[:4000],  # cap to avoid token overflow
    )


def build_translate_name_prompt(company_name: str, country: str, language: str) -> str:
    return TRANSLATE_NAME_TEMPLATE.format(
        company_name=company_name,
        country=country,
        language=language,
    )
