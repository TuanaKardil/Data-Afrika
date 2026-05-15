"""AfriEnrich CLI — enrich, verify, and report on African importer data."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import structlog
import typer
from dotenv import load_dotenv

load_dotenv()

from .ai.judge import Judge
from .cache import Cache
from .excel.reader import read_input
from .excel.writer import StagingWriter, finalize, load_staging
from .models import AuditEntry
from .pipeline import RunConfig, process_row
from .utils.logging_setup import setup_logging
from .validators.duplicate_detector import DuplicateDetector
from .validators.quality_filters import QualityFilter

app = typer.Typer(add_completion=False, help="Enrich African importer companies with contact data.")
log = structlog.get_logger()


@app.command()
def enrich(
    input_path: Annotated[Path, typer.Argument(help="Input XLSX file")],
    output: Annotated[  # noqa: E501
        Path | None, typer.Option("-o", "--output", help="Output XLSX path")
    ] = None,
    country: Annotated[
        str | None, typer.Option("-c", "--country", help="Override country (ISO2)")
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume/--no-resume", help="Resume from partial output")
    ] = True,
    retry_not_found: Annotated[
        bool, typer.Option("--retry-not-found", help="Retry not_found rows")
    ] = False,
    smtp_probe: Annotated[
        bool, typer.Option("--smtp-probe", help="Enable SMTP RCPT email probing")
    ] = False,
    no_cache: Annotated[bool, typer.Option("--no-cache", help="Bypass cache reads")] = False,
    high_quality: Annotated[
        bool, typer.Option("--high-quality", help="Use Opus 4.7 for AI judge")
    ] = False,
    max_rows: Annotated[
        int | None, typer.Option("--max-rows", help="Limit rows for testing")
    ] = None,
    concurrency: Annotated[int, typer.Option("--concurrency", help="Concurrent rows")] = 5,
    sources: Annotated[
        str | None, typer.Option("--sources", help="Comma-separated source whitelist")
    ] = None,
    exclude_sources: Annotated[
        str | None, typer.Option("--exclude-sources", help="Comma-separated blacklist")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Validate config and input only")
    ] = False,
) -> None:
    """Enrich an XLSX list of importer companies with phone, email, website, and social data."""

    output_path = output or input_path.with_suffix(".enriched.xlsx")
    staging_path = output_path.with_suffix(".staging.csv")
    log_dir = Path(__file__).parent.parent.parent / "data" / "logs"
    setup_logging(log_dir)

    typer.echo(f"Input:  {input_path}")
    typer.echo(f"Output: {output_path}")

    if not input_path.exists():
        typer.echo(f"Error: input file not found: {input_path}", err=True)
        raise typer.Exit(1)

    companies = read_input(input_path)
    if country:
        companies = [c.model_copy(update={"country": country.upper()}) for c in companies]

    if max_rows:
        companies = companies[:max_rows]

    typer.echo(f"Loaded {len(companies)} companies.")

    if dry_run:
        typer.echo("Dry run complete — config valid, no sources called.")
        raise typer.Exit(0)

    # Resume: skip already-processed rows
    processed_indices: set[int] = set()
    if resume:
        processed_indices = load_staging(staging_path)
        if processed_indices:
            typer.echo(f"Resuming — skipping {len(processed_indices)} already-processed rows.")

    companies_to_run = [
        c for c in companies
        if c.row_index not in processed_indices
    ]
    if not companies_to_run:
        typer.echo("All rows already processed. Run with --no-resume to reprocess.")
        raise typer.Exit(0)

    config = RunConfig(
        output_path=output_path,
        resume=resume,
        smtp_probe=smtp_probe,
        no_cache=no_cache,
        high_quality=high_quality,
        max_rows=max_rows,
        concurrency=concurrency,
        sources_whitelist=[s.strip() for s in sources.split(",")] if sources else [],
        sources_blacklist=(
            [s.strip() for s in exclude_sources.split(",")] if exclude_sources else []
        ),
    )

    asyncio.run(_run_enrich(companies_to_run, config, staging_path, output_path))


async def _run_enrich(
    companies: Sequence[object],
    config: RunConfig,
    staging_path: Path,
    output_path: Path,
) -> None:
    cache = Cache()
    staging_writer = StagingWriter(staging_path)
    duplicate_detector = DuplicateDetector()
    quality_filter = QualityFilter()
    judge = Judge(high_quality=config.high_quality)

    all_audit: list[AuditEntry] = []
    errors: list[dict[str, object]] = []

    sem = asyncio.Semaphore(config.concurrency)

    async def process_one(company: object) -> None:
        async with sem:
            try:
                enriched, audit_entries = await process_row(
                    company,  # type: ignore[arg-type]
                    config,
                    cache,
                    staging_writer,
                    duplicate_detector,
                    judge,
                    quality_filter,
                )
                all_audit.extend(audit_entries)
            except Exception as exc:
                log.error(
                    "unhandled_row_error",
                    company=getattr(company, "importer_name", "?"),
                    error=str(exc),
                )
                errors.append({
                    "company": getattr(company, "importer_name", "?"),
                    "row_index": getattr(company, "row_index", -1),
                    "error": str(exc),
                })

    tasks = [process_one(c) for c in companies]
    total = len(tasks)

    for i, coro in enumerate(asyncio.as_completed(tasks), 1):
        await coro
        if i % 10 == 0 or i == total:
            typer.echo(f"Progress: {i}/{total}")

    # Flag intermediary numbers across run
    flagged = duplicate_detector.get_flagged()
    if flagged:
        log.warning("intermediary_phones_detected", count=len(flagged), numbers=list(flagged)[:5])

    staging_writer.close()
    cache.close()

    finalize(staging_path, output_path, all_audit, errors)
    typer.echo(f"\nDone. Output: {output_path}")
    typer.echo(f"AI judge total cost: ${judge.run_cost:.4f}")


@app.command()
def verify(
    output_path: Annotated[Path, typer.Argument(help="Enriched XLSX to re-validate")],
) -> None:
    """Re-validate phones and emails in an enriched XLSX."""
    import pandas as pd

    from .models import PhoneResult
    from .validators.phone_validator import validate_phones

    if not output_path.exists():
        typer.echo(f"File not found: {output_path}", err=True)
        raise typer.Exit(1)

    df = pd.read_excel(output_path, sheet_name="results")
    total = len(df)
    _skip = ("_source", "_confidence", "_type", "_audit_id")
    phone_cols = [c for c in df.columns if c.startswith("phone_") and not c.endswith(_skip)]

    verified = 0
    for col in phone_cols:
        col_num = col.split("_")[1]
        source_col = f"phone_{col_num}_source"
        for _, row in df.iterrows():
            raw = row.get(col, "")
            if not raw or not isinstance(raw, str):
                continue
            phones = [PhoneResult(number=raw, source=row.get(source_col, ""), confidence=70)]
            validated, _ = validate_phones(phones, row.get("country_iso2", ""))
            if validated:
                verified += 1

    typer.echo(f"Verified {verified} phone(s) across {total} rows.")


@app.command()
def stats(
    output_path: Annotated[Path, typer.Argument(help="Enriched XLSX for stats report")],
) -> None:
    """Print enrichment statistics from an output XLSX."""
    import pandas as pd

    if not output_path.exists():
        typer.echo(f"File not found: {output_path}", err=True)
        raise typer.Exit(1)

    results_df = pd.read_excel(output_path, sheet_name="results")
    total = len(results_df)

    if total == 0:
        typer.echo("No rows found.")
        return

    status_counts = results_df["search_status"].value_counts()
    enriched = status_counts.get("enriched", 0)
    partial = status_counts.get("partial", 0)
    not_found = status_counts.get("not_found", 0)
    phone_rate = (results_df["phone_1"].notna() & (results_df["phone_1"] != "")).mean() * 100
    email_rate = (results_df["email_1"].notna() & (results_df["email_1"] != "")).mean() * 100
    avg_conf = results_df["overall_confidence"].mean()

    typer.echo(f"\n{'='*40}")
    typer.echo(f"  AfriEnrich Stats — {output_path.name}")
    typer.echo(f"{'='*40}")
    typer.echo(f"  Total rows      : {total}")
    typer.echo(f"  Enriched        : {enriched} ({enriched/total*100:.1f}%)")
    typer.echo(f"  Partial         : {partial} ({partial/total*100:.1f}%)")
    typer.echo(f"  Not found       : {not_found} ({not_found/total*100:.1f}%)")
    typer.echo(f"  Phone hit rate  : {phone_rate:.1f}%")
    typer.echo(f"  Email hit rate  : {email_rate:.1f}%")
    typer.echo(f"  Avg confidence  : {avg_conf:.0f}")
    typer.echo(f"{'='*40}\n")

    try:
        source_df = pd.read_excel(output_path, sheet_name="source_stats")
        if not source_df.empty:
            typer.echo("Source stats:")
            typer.echo(source_df.to_string(index=False))
    except Exception:
        pass


def main() -> None:
    app()


if __name__ == "__main__":
    main()
