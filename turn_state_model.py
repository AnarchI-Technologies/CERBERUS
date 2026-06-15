class CerberusState:
    hp: int
    ep: int
    atk: int
    defense: int

    position: str
    region_map: dict

    enemies_visible: list
    items_visible: list

    deathzones: list
    pending_deathzones: list

    cooldown_active: bool