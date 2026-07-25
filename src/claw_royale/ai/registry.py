from typing import Callable, Dict, List

# Import the functions from each of the isolated strategy modules
from .strategies import (
    combat,
    economy,
    progression,
    quest,
    social,
    utility,
    owner_command,
    memory,
    learned_policy,
)

# This registry maps a strategy ID to the function that implements it.
# This is the single source of truth for all agent behaviors.
STRATEGY_REGISTRY: Dict[str, Callable] = {
    # Combat
    "combat.evaluate_fight_or_flight": combat.evaluate_fight_or_flight,
    "combat.choose_best_target": combat.choose_best_target,
    
    # Economy
    "economy.assess_resource_needs": economy.assess_resource_needs,
    "economy.find_best_value_pickup": economy.find_best_value_pickup,
    
    # Progression
    "progression.evaluate_ruin_entry": progression.evaluate_ruin_entry,
    
    # Quest
    "quest.evaluate_active_quests": quest.evaluate_active_quests,
    
    # Social
    "social.process_incoming_messages": social.process_incoming_messages,
    
    # Utility
    "utility.evaluate_utility_items": utility.evaluate_utility_items,
    
    # Owner Command
    "owner_command.parse_and_execute": owner_command.parse_and_execute,
    
    # Memory & Learning
    "memory.record_game_outcome": memory.record_game_outcome,
    "memory.update_strategy_effectiveness": memory.update_strategy_effectiveness,
    "learned_policy.adapt_strategy_choice": learned_policy.adapt_strategy_choice,
}

def get_all_strategies() -> List[Callable]:
    """Returns a list of all strategy functions for the decision engine."""
    return list(STRATEGY_REGISTRY.values())

def get_strategy_by_id(strategy_id: str) -> Callable:
    """Retrieves a specific strategy function by its ID."""
    return STRATEGY_REGISTRY[strategy_id]
