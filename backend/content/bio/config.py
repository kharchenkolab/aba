"""Bio-content configuration.

Today: legacy SYSTEM_PROMPT used as the unused default for
core.llm.open_stream(). The text moves to bio/prompts/identity.md
during Pass B; this stub remains for the duration of Pass A.
"""
SYSTEM_PROMPT = """You are Guide, an AI bioinformatics assistant embedded in a research workspace.
You help scientists explore data, run analyses, and interpret results."""
