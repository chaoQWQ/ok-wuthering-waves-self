import hashlib
import json
import unicodedata


def workflow_content_id(workflow, observations, occurrence=0):
    if occurrence < 0:
        raise ValueError('内容出现次数不能为负数')
    indexed = {item.frame_id: item for item in observations}
    first = indexed[workflow.steps[0].evidence_before]
    last = indexed[workflow.steps[-1].evidence_after]

    def text(value):
        return unicodedata.normalize('NFC', value).strip()

    def condition(value):
        return {'scene': value.scene, 'objective': text(value.objective),
                'interaction': text(value.interaction), 'texts': sorted(text(item) for item in value.texts)}

    def boundary(value):
        return {'scene': value.scene, 'objective': text(value.objective), 'interaction': text(value.interaction)}

    identity = {
        'version': 2, 'cycle': workflow.cycle,
        'event_period': workflow.period if workflow.cycle == 'event' else '',
        'language': workflow.language, 'title': text(workflow.quest_title or workflow.title),
        'first': boundary(first), 'last': boundary(last), 'occurrence': occurrence,
        'steps': [{'action': step.action, 'target': text(step.target),
                   'preconditions': condition(step.preconditions), 'completion': condition(step.completion)}
                  for step in workflow.steps],
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return 'learned_v2_' + digest[:32]
