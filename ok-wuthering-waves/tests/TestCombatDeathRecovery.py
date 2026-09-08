import ast
import unittest
from pathlib import Path


def _method(path, class_name, method_name):
    module = ast.parse(Path(path).read_text(encoding='utf-8'))
    class_node = next(
        node for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node for node in class_node.body
        if isinstance(node, ast.FunctionDef) and node.name == method_name
    )


def _self_calls(method_node):
    return [
        node for node in ast.walk(method_node)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == 'self'
    ]


class TestCombatDeathRecovery(unittest.TestCase):
    def test_auto_combat_attempts_recovery_after_character_death(self):
        method = _method('src/task/AutoCombatTask.py', 'AutoCombatTask', 'run')
        char_dead_handler = next(
            handler for handler in ast.walk(method)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == 'CharDeadException'
        )
        self.assertIn('revive_action', [call.func.attr for call in _self_calls(char_dead_handler)])

    def test_farm_echo_closes_death_popup_before_scene_branch(self):
        method = _method('src/task/FarmEchoTask.py', 'FarmEchoTask', 'revive_action')
        close_call = next(
            call for call in _self_calls(method)
            if call.func.attr == 'close_revive_popup'
        )
        realm_branch = next(
            node for node in ast.walk(method)
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and node.test.attr == '_in_realm'
        )
        self.assertLess(close_call.lineno, realm_branch.lineno)

    def test_tacet_recovery_is_bounded_and_reenters_from_book(self):
        method = _method('src/task/TacetTask.py', 'TacetTask', 'farm_tacet')
        self.assertTrue(any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == 'max_recovery_retries'
                    for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and node.value.value == 3
            for node in ast.walk(method)
        ))
        self.assertTrue(any(
            isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == 'CharRevivedException'
            for handler in ast.walk(method)
        ))

    def test_nightmare_death_recovery_skips_after_three_attempts(self):
        method = _method('src/task/NightmareNestTask.py', 'NightmareNestTask', 'combat_nest')
        self.assertTrue(any(
            isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name)
            and node.left.id == 'attempts'
            and any(isinstance(op, ast.GtE) for op in node.ops)
            and any(isinstance(value, ast.Constant) and value.value == 3
                    for value in node.comparators)
            for node in ast.walk(method)
        ))

    def test_revive_world_transition_allows_slow_loading(self):
        method = _method('src/task/BaseCombatTask.py', 'BaseCombatTask', '_travel_to_nearest_waypoint')
        waits = [
            call for call in _self_calls(method)
            if call.func.attr == 'wait_in_team_and_world'
        ]
        self.assertTrue(any(
            any(keyword.arg == 'time_out'
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value >= 120
                for keyword in call.keywords)
            for call in waits
        ))


if __name__ == '__main__':
    unittest.main()
