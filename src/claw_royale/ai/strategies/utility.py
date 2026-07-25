from typing import Any, Dict, Optional

def evaluate_utility_items(game_state: Any) -> Optional[Dict[str, Any]]:
    """Decides if a utility item (e.g., a scanner) should be used."""
    if game_state.get("fog_of_war_heavy") and game_state.get("has_scanner"):
        return {"action_type": "USE_ITEM", "item_id": "scanner_id"}
    return None
