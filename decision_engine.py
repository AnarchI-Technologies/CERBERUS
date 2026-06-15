def decide(state):

    # 1. survival first
    if avoid_deathzones(state):
        return move_safe()

    # 2. high value target
    target = rank_threats(state.enemies_visible)[0]

    # 3. if kill is viable
    if should_attack(target, state):
        return attack(target)

    # 4. opportunistic loot
    if state.items_visible:
        return pickup_best_item()

    # 5. positional advantage
    return move_toward_center_pressure()