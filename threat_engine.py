def threat_score(enemy):
    return (
        enemy.hp * 0.3 +
        enemy.atk * 0.3 +
        enemy.kills * 0.4
    )


def rank_threats(enemies):
    return sorted(enemies, key=threat_score, reverse=True)