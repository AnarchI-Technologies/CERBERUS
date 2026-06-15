def predator_mode(state):

    if len(state.enemies_visible) <= 3:

        return {
            "behavior": "hunt",
            "priority": "kill_leaders_first",
            "movement": "zone_pressure",
            "risk_tolerance": 0.9
        }