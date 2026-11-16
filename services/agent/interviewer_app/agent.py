"""Entry point discovered by `adk web services/agent`.

ADK looks for a module-level variable named `root_agent` inside `<app_pkg>/agent.py`.
"""

from .orchestrator import InterviewOrchestrator

root_agent = InterviewOrchestrator()
