"""Re-export shim — db.py split across core/graph/* and bio/graph/* during
arch3 Pass B. Callers continue to `from db import …` for the duration of
Pass B; they migrate to the proper imports in Pass C, after which this
file is deleted.

Layout of the split:
- core/graph/_schema     → DB_PATH, WORKSPACE_ID, init_db, _conn, _utcnow,
                            gen_entity_id, _GLOBAL_DISABLED, _column_exists
- core/graph/entities    → CRUD on entities table
- core/graph/edges       → typed entity edges
- core/graph/audit       → events, advisor_notes, context_assemblies,
                            context_suggestions
- core/graph/jobs        → background-job row CRUD
- core/graph/proposals_store → proposals CRUD (framework)
- core/graph/threads     → opaque-typed thread containers
- core/graph/messages    → message log
- core/graph/tool_settings → enable/disable plumbing
- bio/graph/result_members  → result panel-list helpers
- bio/graph/figure_history  → wasRevisionOf chains
- bio/graph/search          → faceted search + find_kept_note
"""
# Schema / connection / constants
from core.graph._schema import (  # noqa: F401
    DB_PATH, WORKSPACE_ID, init_db, _conn, _utcnow, _column_exists,
    gen_entity_id, _GLOBAL_DISABLED,
)
# Entities
from core.graph.entities import (  # noqa: F401
    create_entity, get_entity, list_entities, count_entities, update_entity,
    archive_entity, restore_entity, _row_to_entity,
)
# Edges
from core.graph.edges import (  # noqa: F401
    add_edge, remove_edge, edges_from, edges_to,
)
# Audit / events / advisor notes / context assemblies / suggestions
from core.graph.audit import (  # noqa: F401
    log_event, list_events,
    add_advisor_note, set_advisor_note_status, list_advisor_notes,
    log_context_assembly, session_assembly_summary,
    add_context_suggestion, list_context_suggestions,
    update_context_suggestion_status,
)
# Jobs
from core.graph.jobs import (  # noqa: F401
    _row_to_job, create_job, get_job, list_jobs, update_job,
)
# Proposals
from core.graph.proposals_store import (  # noqa: F401
    _row_to_proposal, proposal_signature_exists,
    add_proposal, list_proposals, get_proposal, update_proposal,
)
# Threads
from core.graph.threads import (  # noqa: F401
    create_thread, list_threads, find_default_thread, get_or_create_default_thread,
)
# Messages
from core.graph.messages import (  # noqa: F401
    append_message, get_messages, clear_messages, get_all_messages, clear_history,
)
# Tool settings
from core.graph.tool_settings import (  # noqa: F401
    get_disabled_tools, set_tool_enabled,
)
# Bio-coupled helpers
from content.bio.graph.result_members import (  # noqa: F401
    _result_members, _save_members,
    add_result_member, remove_result_member, update_result_member,
    reorder_result_members,
)
from content.bio.graph.figure_history import (  # noqa: F401
    find_active_figure_by_title, figure_history,
)
from content.bio.graph.search import (  # noqa: F401
    search, find_kept_note,
)
