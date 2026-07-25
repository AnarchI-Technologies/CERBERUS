from typing import Any, Dict, List, Optional
from .types import MessageEvent

def process_incoming_messages(messages: List[MessageEvent]) -> Optional[Dict[str, Any]]:
    """Reacts to incoming messages from other agents."""
    if not messages:
        return None
    # Simple logic: respond to the first message.
    return {"action_type": "SEND_REPLY", "recipient_id": messages[0].sender_id}
