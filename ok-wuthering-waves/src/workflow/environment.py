from PySide6.QtCore import QLocale


def workflow_language(locale):
    if isinstance(locale, QLocale):
        return locale.name()
    if isinstance(locale, str) and locale:
        return str(locale)
    raise ValueError('无法确定流程使用的语言')
