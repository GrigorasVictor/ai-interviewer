"""Analysis pipeline service (`services/analysis/analysis_app`).

Watches `data/*.json` for completed interviews (`phase == "done"` and `processed == False`),
enriches each file in-place with NLP sentiment/keywords + AI summary + AI insights,
syncs the knowledge graph to Neo4j, and sets `processed = True` (`status = "analyzed"`).
"""

from .pipeline import (
    AnalysisPipeline,
    is_ready_for_analysis,
    process_data_directory,
    process_interview_file,
)

__all__ = [
    "AnalysisPipeline",
    "is_ready_for_analysis",
    "process_data_directory",
    "process_interview_file",
]
