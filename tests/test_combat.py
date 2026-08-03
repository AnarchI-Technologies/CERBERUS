import unittest
from src.claw_royale.ai.strategies.combat import evaluate_fight_or_flight, choose_best_target
from src.claw_royale.ai.strategies.types import CombatContext, Unit

class TestCombatStrategies(unittest.TestCase):

    def test_evaluate_fight_or_flight(self):
        """Tests the decision to attack or retreat based on tactical advantage."""
        # High advantage should result in an attack action
        attack_context = CombatContext(friendly_units=[], enemy_units=[], can_retreat=True, tactical_advantage=2.0)
        self.assertEqual(evaluate_fight_or_flight(attack_context)["action_type"], "ATTACK_PRIORITY")

        # Low advantage should result in a retreat action
        retreat_context = CombatContext(friendly_units=[], enemy_units=[], can_retreat=True, tactical_advantage=0.5)
        self.assertEqual(evaluate_fight_or_flight(retreat_context)["action_type"], "RETREAT")

        # Neutral advantage should result in no action
        hold_context = CombatContext(friendly_units=[], enemy_units=[], can_retreat=True, tactical_advantage=1.0)
        self.assertIsNone(evaluate_fight_or_flight(hold_context))

    def test_choose_best_target(self):
        """Tests that the strategy targets the lowest health enemy."""
        enemy1 = Unit(id="e1", health=100, max_health=100, damage=10, is_ranged=False, position=(0,0))
        enemy2 = Unit(id="e2", health=50, max_health=100, damage=10, is_ranged=False, position=(0,0)) # Lower health
        enemy3 = Unit(id="e3", health=100, max_health=100, damage=10, is_ranged=False, position=(0,0))

        context = CombatContext(friendly_units=[], enemy_units=[enemy1, enemy2, enemy3], can_retreat=True, tactical_advantage=1.0)

        action = choose_best_target(context)
        self.assertIsNotNone(action)
        self.assertEqual(action["action_type"], "TARGET_UNIT")
        self.assertEqual(action["target_id"], "e2") # Should target the one with 50 health
