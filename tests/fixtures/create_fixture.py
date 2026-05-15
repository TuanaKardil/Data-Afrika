"""Script to generate tests/fixtures/sample_input.xlsx."""

from pathlib import Path

import pandas as pd

rows = [
    {"importer_name": "ACME Cocoa SARL", "country": "CI", "hs_code": "1801", "category_hint": "cacao"},  # noqa: E501
    {"importer_name": "Lagos Imports Ltd", "country": "NG", "hs_code": "2709", "category_hint": "petroleum"},  # noqa: E501
    {"importer_name": "Dakar Trading SA", "country": "SN", "hs_code": "1001", "category_hint": "wheat"},  # noqa: E501
    {"importer_name": "Accra Foods Limited", "country": "GH", "hs_code": "0901", "category_hint": "coffee"},  # noqa: E501
    {"importer_name": "Nairobi Steel Works", "country": "KE", "hs_code": "7208", "category_hint": "steel"},  # noqa: E501
    {"importer_name": "Abidjan Export SARL", "country": "CI", "hs_code": "4403", "category_hint": "timber"},  # noqa: E501
    {"importer_name": "Port Harcourt Oil LTD", "country": "NG", "hs_code": "2710", "category_hint": "oil"},  # noqa: E501
    {"importer_name": "Thiès Textiles SUARL", "country": "SN", "hs_code": "5208", "category_hint": "textiles"},  # noqa: E501
    {"importer_name": "Kumasi Agro SA", "country": "GH", "hs_code": "1201", "category_hint": "soy"},
    {"importer_name": "Mombasa Logistics Ltd", "country": "KE", "hs_code": "8704", "category_hint": "trucks"},  # noqa: E501
]

df = pd.DataFrame(rows)
out = Path(__file__).parent / "sample_input.xlsx"
df.to_excel(out, index=False)
print(f"Created: {out}")


if __name__ == "__main__":
    pass
