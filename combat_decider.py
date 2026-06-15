def should_attack(self, enemy, state):

    expected_damage = (
        state.atk - (enemy.defense * 0.5)
    )

    lethal = expected_damage >= enemy.hp * 0.6

    return (
        lethal and
        state.ep >= 2 and
        enemy.in_range
    )