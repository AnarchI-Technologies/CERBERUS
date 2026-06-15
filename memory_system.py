memory = {
    "enemy_profiles": {},
    "kill_paths": [],
    "zone_history": []
}
def update_enemy(enemy):

    memory["enemy_profiles"][enemy.id] = {
        "aggression": enemy.atk,
        "survivability": enemy.hp,
        "behavior": classify(enemy)
    }