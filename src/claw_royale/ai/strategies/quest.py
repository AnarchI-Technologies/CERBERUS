from typing import Any, Dict, List, Optional
from .types import Quest

def evaluate_active_quests(quests: List[Quest]) -> Optional[Dict[str, Any]]:
    """Prioritizes the most valuable and achievable active quest."""
    if not quests:
        return None
    # Simple logic: prioritize the first available quest.
    active_quest = quests[0]
    return {"action_type": "PURSUE_QUEST", "quest_id": active_quest.id}
