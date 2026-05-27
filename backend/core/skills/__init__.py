"""Skill loader — markdown files with frontmatter declare reusable
procedures. Content packs register a directory; the loader walks it,
parses each .md, and populates the registry. Plan validator consults
the registry to flag unknown skill references."""
from .loader import (
    SkillSpec,
    list_skills,
    get_skill,
    read_skill,
    register_skill_dir,
    skills_index_block,
)

__all__ = [
    "SkillSpec",
    "list_skills",
    "get_skill",
    "read_skill",
    "register_skill_dir",
    "skills_index_block",
]
