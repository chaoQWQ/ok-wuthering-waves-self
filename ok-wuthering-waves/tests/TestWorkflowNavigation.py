from pathlib import Path

import cv2
import pytest

from src.utils.QuestBackgroundMotion import detect_background_motion
from src.workflow.input import WorkflowStopped
from src.workflow.navigation import NavigationController, geometric_reference


IMAGES = Path(__file__).parent / 'images'


def screenshot(name):
    frame = cv2.imread(str(IMAGES / name))
    assert frame is not None
    return frame


def test_real_scene_geometry_tracks_recorded_camera_change():
    before = screenshot('quest_motion_before.png')
    after = screenshot('quest_motion_after.png')
    reference = geometric_reference(before, after)
    assert reference is not None
    assert reference.inliers > 100
    assert reference.reprojection_error < 1
    assert reference.coverage > .5
    assert .005 < reference.center_shift_x < .03
    assert .01 < reference.center_shift_y < .04
    assert .9 < reference.area_ratio < 1.05
    assert reference.target_x is not None
    assert detect_background_motion(before, after).moving is True


def test_real_unrelated_scenes_cannot_supply_direction():
    reference = screenshot('quest_navigation_start.png')
    for name in ('quest_rock_obstruction.png', 'quest_yangyang_interaction.png', 'in_combat.png'):
        assert geometric_reference(reference, screenshot(name)) is None


def test_recorded_beacon_geometry_controls_camera_direction():
    frame = screenshot('quest_navigation_start.png')
    reference = geometric_reference(frame, frame)
    assert reference is not None and reference.target_x < .3
    turn = NavigationController().reference_turn(frame.shape[1], [reference])
    assert turn is not None and turn.action == 'turn'
    assert turn.horizontal < 0
    assert '道路参照截图' in turn.reason


def test_real_beacon_turns_without_forward_input():
    frame = screenshot('quest_navigation_wrong_direction.png')
    command = NavigationController().decide(frame, 73, now=0)
    assert command.action == 'turn'
    assert command.horizontal < 0
    assert command.duration == 0


def test_missing_beacon_searches_camera_with_a_finite_limit():
    frame = screenshot('quest_yangyang_interaction.png')
    controller = NavigationController()
    commands = [controller.decide(frame, None, now=index * .2)
                for index in range(len(controller.SEARCH_OFFSETS))]
    assert all(item.action == 'turn' and item.duration == 0 for item in commands)
    assert sum(item.horizontal for item in commands) == 0
    with pytest.raises(WorkflowStopped, match='原地搜索视角'):
        controller.decide(frame, None, now=3)


def test_continuous_stationary_background_stops_forward_input():
    frame = screenshot('quest_motion_before.png')
    controller = NavigationController()
    assert controller.decide(frame, 5, now=0).action == 'move'
    with pytest.raises(WorkflowStopped, match='环境画面没有移动'):
        for index in range(1, 12):
            controller.decide(frame, 5, now=index * .2)


def test_near_beacon_waits_for_objective_or_interaction_change():
    frame = screenshot('quest_motion_before.png')
    command = NavigationController().decide(frame, 1, now=0)
    assert command.action == 'wait'
    assert command.duration == 0


def test_repeated_distance_increase_does_not_extend_progress_deadline():
    frame = screenshot('quest_navigation_start.png')
    controller = NavigationController()
    assert controller.decide(frame, 39, now=0).action == 'turn'
    assert controller.decide(frame, 40, now=10).action == 'turn'
    with pytest.raises(WorkflowStopped, match='距离进展'):
        controller.decide(frame, 39.8, now=16)


@pytest.mark.parametrize('sensitivity', [0, -1, float('nan'), float('inf')])
def test_invalid_camera_sensitivity_fails_before_navigation(sensitivity):
    with pytest.raises(ValueError, match='灵敏度'):
        NavigationController(sensitivity)
