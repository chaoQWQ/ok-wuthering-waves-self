from PySide6.QtCore import QLocale

from src.workflow.environment import workflow_language


def test_language_identity_survives_new_qlocale_instances():
    original = QLocale('zh_CN')
    reopened = QLocale('zh_CN')
    assert str(original) != str(reopened)
    assert workflow_language(original) == workflow_language(reopened) == 'zh_CN'
    assert workflow_language(QLocale('en_US')) == workflow_language('en_US') == 'en_US'
