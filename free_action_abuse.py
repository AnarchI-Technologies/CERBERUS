def free_action_phase(state):

    actions = []

    if best_weapon_available(state):
        actions.append(equip_best_weapon())

    if loot_nearby(state):
        actions.append(pickup())

    if enemy_nearby(state):
        actions.append(talk("scouting bait message"))

    return actions