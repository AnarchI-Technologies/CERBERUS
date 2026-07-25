from typing import Any, Dict, Optional

def parse_and_execute(command: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parses a command from the owner and translates it into an action."""
    command_text = command.get("text", "").lower()
    if "attack" in command_text:
        return {"action_type": "ATTACK_IMMEDIATE", "priority": "critical"}
    return None
