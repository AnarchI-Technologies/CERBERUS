def ep_priority(state):

    if state.ep >= 6:
        return "AGGRESSIVE"

    if state.ep <= 2:
        return "STALL_AND_RECOVER"

    return "BALANCED"