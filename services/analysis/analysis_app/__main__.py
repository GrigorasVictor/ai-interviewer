"""CLI entry point (`python -m analysis_app`)."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time

from .pipeline import AnalysisPipeline


def _configure_logging() -> None:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(levelname)s - [%(name)s] %(message)s",
    )


def main() -> None:
    _configure_logging()
    logger = logging.getLogger("analysis_app")

    parser = argparse.ArgumentParser(
        description="Run the Interview Analysis Pipeline + Neo4j Graph Sync on data/*.json."
    )
    parser.add_argument("--watch", action="store_true", help="Continuously watch data/ for completed interviews.")
    parser.add_argument("--force", action="store_true", help="Re-analyze files even if processed=True.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic fallback instead of calling LLM.")
    args = parser.parse_args()

    pipeline = AnalysisPipeline(use_llm=not args.no_llm)
    if not args.watch:
        summary = pipeline.process_directory(force=args.force)
        print(json.dumps(summary, indent=2))
        return

    logger.info(
        "Watching directory '%s' every %ss for interviews with phase='done' and processed=False...",
        pipeline.settings.data_dir,
        pipeline.settings.poll_interval_seconds,
    )
    while True:
        summary = pipeline.process_directory(force=args.force)
        if summary["analyzed"]:
            logger.info("Batch completed -> analyzed=%s", summary["analyzed"])
        time.sleep(pipeline.settings.poll_interval_seconds)


if __name__ == "__main__":
    main()
