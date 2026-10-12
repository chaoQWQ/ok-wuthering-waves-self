# 当前仓库 API 与工坊保存

以下内容依据 2026-10-12 的本地实现。应用技能时重新阅读目标函数，当前源码决定调用方式。

## 代码入口

相对于仓库根目录：

- `ok-wuthering-waves/src/char/BaseChar.py`：角色动作、技能释放、资源识别及 `SwitchPriority`。
- `ok-wuthering-waves/src/task/BaseCombatTask.py`：切人目标选择、切人执行、队伍识别及冻结时间计算。
- `ok-wuthering-waves/src/char/CharFactory.py`：class name、识别名称、角色配置及 `apply_team_char_classes()`。
- `ok-wuthering-waves/src/char/CustomCharLoader.py`：工坊目录、代码验证、保存、加载及分享包格式。
- `ok-wuthering-waves/src/gui/CharacterCodeTab.py`：工坊编辑、保存和运行中加载行为。
- `ok-wuthering-waves/tests/TestCustomCharLoader.py`：工坊规则的现有测试；阅读其中验证的行为，新增验证使用真实代码和真实文件。

## 队伍身份与加载

`normalize_team(team)` 接受角色类或英文 class name，要求三个不同角色，按 `str.casefold` 排序。`get_team_key(team)` 使用 `__` 连接排序后的 class name。运行中的槽位顺序由角色实例的 `index` 表示。

工坊目录由 `Config.config_folder` 决定，路径为 `custom_teams/<team_key>/`。当前应用配置使用 `configs`，启动工作目录影响实际位置，因此调用 `get_custom_team_folder(team)` 确认路径。

每个队伍包含 `team.json` 和三个 `<ClassName>.py`。Python 文件必须定义同名的顶层 class，该 class 必须继承 `BaseChar`。保留原始英文 class name、角色识别名称及当前 import 路径。

`apply_team_char_classes()` 在完整三人队伍识别后应用队伍类。类发生替换时，当前实现把既有实例的 `__dict__` 复制到新实例；新增阶段状态需要考虑首次访问、重新识别和重新加载，不能假定新增构造函数字段始终存在。

制作自定义队伍轴时使用工坊三个角色文件。新增内置角色时才涉及 `CharFactory.py` 的登记流程。

## 角色动作与切人

主要修改入口是 `do_perform()`；`perform()` 负责通用执行时间记录。`reset_state()` 会刷新部分状态，阅读调用位置后确定队伍阶段的保存和重置方式。

| API | 当前实现要点 |
| --- | --- |
| `click_resonance(...)` | 返回 tuple，首项表示点击结果；参数 `time_out=0` 使用内部超时，需阅读当前循环 |
| `click_liberation(...)` | 返回 bool，包含演出检测、等待和冻结时间记录；需核查其成功判据适用于目标角色 |
| `click_echo(duration=0, sleep_time=0, time_out=1)` | 声骸调用；`time_out=0` 的行为需阅读内部循环 |
| `continues_normal_attack(duration, ...)` | 执行固定时长普通攻击，可使用 `until_con_full`；实际资源结果另行确认 |
| `heavy_attack(duration=0.6)` | 使用 `mouse_down()`、等待和 `mouse_up()`；自定义持续按住动作要保证异常清理 |
| `is_con_full()` / `get_current_con()` | 核查当前角色协奏；读取对象与画面应对应 |
| `switch_next_char(...)` | 由任务选择目标并执行切人；完成后应确认阶段要求的目标和变奏状态 |
| `get_switch_priority(current_char, has_intro, target_low_con)` | 在候选目标角色上调用，描述该角色是否应接替当前角色 |
| `time_elapsed_accounting_for_freeze(start, ...)` | 按项目记录扣除冻结时间，适用于受冻结影响的冷却与增益计算 |

`SwitchPriority` 在 `BaseChar.py` 定义，当前值依次为 `NO=0`、`LOW=100`、`NORMAL=200`、`HIGH=300`、`MUST=400`。切人还受到治疗角色的满协奏切出限制、目标切人冷却及队伍增益规则影响；阅读 `_choose_switch_target()` 及其调用函数后制定阶段优先级。

`has_intro` 表示入场状态；`free_intro=True` 会影响切人阶段对变奏条件的判断，只有具备实际机制依据时才能使用。输入槽位使用运行时 `index`，文件名排序不决定切人次序。

`BaseChar.click()` 当前强制设置 `down_time=0.01`。需要特定按住时长时查看 `task.click()` 的完整签名和调用行为，结合真实录像验证。调用成功还需检查动作证据。

## 保存、导入副本与导出

`create_custom_team(team)` 以当前内置代码创建三个角色文件。修改已有队伍时读取其当前内容，并保存可恢复副本。

工坊区分当前 `<ClassName>.py`、保存的 `<ClassName>.import.py` 与 manifest 的 `code_mode`。`save_team_code_as_import(team, codes)` 接受 `{class_name: code}`，将整个队伍的当前代码保存为导入代码并切换到导入模式；编辑一个角色时也涉及整个队伍的副本状态。

`save_team_char_code()` 验证并写入一个当前角色文件；它与工坊“保存为导入代码”的行为需要分别理解。直接编辑文件之后，在验证运行与导出之前确认代码模式、当前文件和导入副本内容一致。

`export_custom_team()` 在队伍拥有导入副本时导出导入副本。因此导出后必须读取实际 ZIP 内容，确认分享的是经过验证的三个角色文件。

ZIP 根目录只包含 `team.json` 和三个 `<ClassName>.py`。manifest 的 `name`、`description`、`author`、`version`、`team` 必须是非空字符串；`team` 使用按字母排序的英文显示名称，以逗号分隔。显示名称通过 `get_english_char_name()` 获取，例如 `Linnai` 对应 `Lynae`，`ShoreKeeper` 对应 `Shorekeeper`。

使用 `zipfile` 与 `json` 检查真实分享包，调用 `inspect_team_archive()` 验证格式。该函数要求 class 定义与文件名匹配，并检查路径、成员列表、编码及大小。实际 class 加载还需确认继承关系与依赖可用性。

`load_team_char_class()` 会记录加载异常并返回内置 class。验证实际应用时必须确认日志、模块路径或实例类型，发现加载异常即报告验证失败。

## 验证范围

在项目本地 `.venv` 中验证 Python 语法、真实 import、class 继承和真实 ZIP 包。识别函数使用真实截图运行；循环阶段使用真实日志与实机画面验证。

源码检查与包校验完成后，仍需真实运行证明动作时序、资源到账和循环接续。仅授权整理技能时，验证技能文件和引用资料；实际队伍制作与账号操作在后续请求范围内执行。
