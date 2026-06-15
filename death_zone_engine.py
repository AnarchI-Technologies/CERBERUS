def avoid_deathzones(state):
    unsafe = state.pending_deathzones + state.deathzones

    if state.position in unsafe:
        return "MOVE_SAFE_ZONE"

    return None