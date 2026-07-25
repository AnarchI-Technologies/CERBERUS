from typing import Any, Dict, List, Optional
from .types import CombatContext

def evaluate_fight_or_flight(context: CombatContext) -> Optional[Dict[str, Any]]:
    """Decides whether to engage, disengage, or hold position."""
    if context.tactical_advantage > 1.5:
        return {"action_type": "ATTACK_PRIORITY", "confidence": 0.8}
    if context.tactical_advantage < 0.7 and context.can_retreat:
        return {"action_type": "RETREAT", "confidence": 0.9}
    return None

def choose_best_target(context: CombatContext) -> Optional[Dict[str, Any]]:
    """Selects the optimal target from a list of enemies."""
    if not context.enemy_units:
        return None
    
    # Simple logic: target the enemy with the lowest health.
    best_target = min(context.enemy_units, key=lambda u: u.health)
    return {"action_type": "TARGET_UNIT", "target_id": best_target.id}
