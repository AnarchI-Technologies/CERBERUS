from typing import Any, Dict, Optional

def evaluate_ruin_entry(game_state: Any) -> Optional[Dict[str, Any]]:
    """Determines if it's safe and valuable to enter a nearby ruin."""
    if game_state.get("nearby_ruin") and not game_state.get("nearby_enemies"):
        return {"action_type": "ENTER_RUIN", "confidence": 0.75}
    return None
