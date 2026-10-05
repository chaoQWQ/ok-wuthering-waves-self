from ok import Box
from ok.feature.FeatureSet import FeatureSet
from pathlib import Path

from config import config

from src.utils.QuestOcrPrivacy import quest_goal_from_lines, sanitize_quest_text
from src.utils.QuestVision import detect_interact_action, parse_distance_text, resize_quest_text_frame


def read_interaction_observations(frame, engine):
    height, width = frame.shape[:2]
    sx, sy = int(width * .55), int(height * .38)
    text_frame, scale = resize_quest_text_frame(frame)
    region = text_frame[round(sy * scale):round(int(height * .70) * scale),
                        round(sx * scale):round(int(width * .92) * scale)]
    boxes = []
    for points, (name, confidence) in engine.ocr(region, cls=False)[0]:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        boxes.append(Box(sx + round(min(xs) / scale), sy + round(min(ys) / scale),
                         round((max(xs) - min(xs)) / scale), round((max(ys) - min(ys)) / scale),
                         name=name, confidence=confidence))
    matching = config["template_matching"]
    features = FeatureSet(False, str(Path(__file__).resolve().parent.parent / "assets/coco_annotations.json"),
                          matching["default_horizontal_variance"], matching["default_vertical_variance"],
                          feature_processor=matching["feature_processor"],
                          hcenter_features=matching["hcenter_features"], vcenter_features=matching["vcenter_features"])
    region_box = Box(sx, sy, int(width * .92) - sx, int(height * .70) - sy)
    matches = features.find_one_feature(frame, "pick_up_f_hcenter_vcenter", box=region_box, threshold=.8)
    key = max(matches, key=lambda match: match.confidence) if matches else None
    key_box = None if key is None else (key.x, key.y, key.width, key.height)
    interaction = detect_interact_action(frame, ocr_boxes=boxes, key_box=key_box)
    panel = text_frame[round(int(height * .23) * scale):round(int(height * .43) * scale),
                       round(int(width * .01) * scale):round(int(width * .25) * scale)]
    lines = [name for points, (name, _) in engine.ocr(panel, cls=False)[0]
             if min(point[0] for point in points) / scale + width * .01 <= width * .05]
    goal = quest_goal_from_lines(lines)
    distances = [parse_distance_text(line, require_unit=True) for line in lines]
    distance = next((value for value in distances if value is not None), None)
    return goal, interaction, distance, sanitize_quest_text("\n".join(lines + [box.name for box in boxes]))
