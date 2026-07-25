from typing import Any, Dict, List, Optional

def assess_resource_needs(game_state: Any) -> Optional[Dict[str, Any]]:
    """Evaluates if the agent is low on a critical resource."""
    resources = game_state.get("resources", {})
    if resources.get("minerals", 0) < 100:
        return {"action_type": "GATHER_MINERALS", "priority": "high"}
    return None

def find_best_value_pickup(game_state: Any) -> Optional[Dict[str, Any]]:
    """Identifies the most valuable item to pick up in the current area."""
    # This is a placeholder for more complex logic.
    items = game_state.get("ground_items", [])
    if items:
        return {"action_type": "PICKUP_ITEM", "item_id": items[0].get("id")}
    return None
