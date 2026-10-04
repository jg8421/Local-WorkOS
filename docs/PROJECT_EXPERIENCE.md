# 项目经验与执行记录

项目经验让同一项目的后续任务沿用用户明确表达的工作偏好，同时保留每轮实际执行记录。它不把模型草稿变成公司事实，也不替用户扩大资料选择。实现位于 `workos/project_experience.py`，不需要模型调用。

## 什么会被记录和复用

每个项目默认开启学习。已持久化的对话轮次会记入本地执行记录，区分 `completed`、`needs_input`、`failed`、`cancelled`、`interrupted`。操作回执保留已完成动作的名称与记录编号；可选的执行阶段只保留已允许的 `stage/detail/status`，最多50条。不保存模型内部推理，也不将执行日志发送给模型。

手动保存、资料导入、任务创建/更新/删除及其他记录提交也会在 Store 事务成功后通知项目审计服务。结构化回执包含 `collection/record_id/action/project_id`，项目本身的操作关联自身编号，删除保留原项目归属。审计只保存操作摘要和记录编号，不复制资料正文、修订正文或模型答案，不从这些事件学习偏好或事实。记录操作和对话轮次是两种不同粒度：一次 AI 轮次可以另有数次真实记录提交。

Store 原有活动列表仍限最近200条；独立项目审计不按这一限制清除。启动时可幂等回填现存活动的明确项目关联，旧活动缺少操作类型或记录编号时保留为未知，不按标题猜测；已裁剪或没有项目关联的更早历史无法重建。删除项目后保留只读历史供本地备份，但不存在的项目不能进入任务上下文。

只有用户原话中的工作方式可以自动形成偏好。明确持久意图，例如“以后每次都先列结论”“整个项目的所有任务都用中文”，可直接成为 `active` 偏好。单次“简洁一点”成为 `pending` 候选。识别范围限定在表达长度、结论先行、来源归属、实际与预测、币种单位期间、当前编辑稿、保留版本、表格、页数和语言。材料正文及助手答复都不能触发学习；混有明确业务数字陈述的句子不会自动变成偏好。

等待补充、失败、中止的轮次只记执行状态，不学习其未完成条件或输出。已完成估值的 `assumptions/calculation` 快照也不会自动成为记忆。因此“成功保存”不等于“事实得到验证”。

每条经验保留 `workspace/project_id/purpose`、来源对话与轮次、创建时间和修订历史。同项目的用途默认隔离；只有用户明确要求整个项目或所有任务沿用的工作规则才进入 `general`。后续同用途、同规则类型的明确持久偏好会停用旧偏好，保留出处；单次候选不会挤掉有效偏好。当前直接要求始终优先。

## 类型与用户控制

| kind | 含义 | 生效条件 |
|---|---|---|
| `preference` | 用户工作偏好 | 明确持久指令自动生效；单次候选或手工记录由用户启用 |
| `lesson` | 用户确认的工作经验 | 手工添加、显式启用；不代表公司事实 |
| `fact` | 用户确认的资料摘录或主张 | 必须关联同项目研究资料及实际存在的原文摘录；用户显式启用 |
| `calculation_basis` | 用户确认的测算口径 | 显式启用；无资料时仅限估值用途，标明用户口径、非公司事实 |

状态为 `pending`、`active`、`disabled`。用户可以查看出处、编辑内容、启用、停用和删除。事实内容或证据修改后会回到待确认，除非用户在同次修改中显式启用。删除经验会清除该条内容及修订历史，并保留不含正文的删除指纹，避免再次扫描同一轮历史时重建该条经验。执行记录和原对话属于各自的可追溯历史，删除经验不会同时删除原对话。

关闭项目经验会停止新的偏好学习及全部上下文复用，继续记录执行状态。再次开启恢复仍有效的经验，不补学关闭期间的旧轮次。

## 事实边界与资料范围

事实证据拒绝个人记忆、其他项目和无所属项目的材料。服务重新计算资料内容与块信息的指纹，不依赖客户端可能过期的 `hash`。来源被编辑、删除、移出项目或改为个人记忆后，相关事实不会继续进入上下文。再次核对当前原文并确认后才能恢复。

复用事实必须满足：本次已经选中全部证据资料、资料指纹未改变、用途相同。服务返回忽略原因，例如 `not_selected`、`changed`、`missing`、`scope_changed`、`context_budget`，不会偷偷加入未选资料。原文存在只能证明摘录来自该材料，不能自动证明用户主张真实。

空证据的 `calculation_basis` 只能由用户明确保存并启用，限定估值用途，标签为用户测算口径、非事实核验。它不能作为公司研究来源或跨用途事实。模型生成的假设不会自动进入此类型。

上下文只包含有效的已启用经验，默认6000字，最多8000字。日志、待确认候选、停用规则及无效来源不进入模型。经验不授予工具权限，不绕过现有资料、模型、工作区及任务校验。

## 服务接口

```python
experience = ProjectExperience(data_dir / 'project-learning.sqlite3', stores, conversations)
experience.observe_turn(workspace, conversation_id, turn_id)
experience.observe_activity(workspace, activity)
experience.status(workspace, project_id, purpose=None, limit=50)
experience.update_settings(workspace, project_id, enabled)
experience.create(workspace, project_id, body)
experience.edit(workspace, project_id, entry_id, body)
experience.set_status(workspace, project_id, entry_id, status)
experience.delete(workspace, project_id, entry_id)
experience.context(workspace, project_id, purpose, source_ids, max_chars=6000)
experience.backup(workspace)
experience.restore(workspace, backup)
experience.close()
```

`observe_turn` 只接受编号，从已持久化的 `Conversations` 读取实际轮次；同轮次重试幂等。新成功主记录先完成，再记录学习；学习写入失败不能使已保存的工作结果丢失或引发重新生成。历史补录仅处理近期100轮内的明确轮次，不自动遍历私有文件夹。

`observe_activity` 校验实际已提交的 Store 活动，同一活动编号幂等。`event.origin` 为 `conversation` 或 `record_change`；后者不携带对话或模型正文。初始化挂钩在私有经验服务创建后安装；通知异常只记录泛化警告，不回滚主记录。访问原文的经验方法统一先取得 Store 锁再取得经验锁，避免提交回调与来源核对互相等待。

`create` 接受 `kind/purpose/title/content/rule_key/evidence/confirm`。证据形式为 `[{document_id, quote}]`。`edit` 接受 `title/content/evidence/status`，不允许改项目、工作区、出处或用途。`status` 返回 `{settings,counts,entries,events,truncated}`；`context` 返回 `{enabled,text,entries,omitted}`。每条公开证据附可读摘录，完整备份另保留内部完整性指纹。

SQLite 中 `experience_settings` 存项目开关，`experience_events` 存幂等执行轮次，`experience_entries` 存经验及出处，`experience_suppressed` 存删除指纹。均按工作区和项目隔离，采用 WAL 与事务。备份格式为 `workos-project-experience`，版本1；恢复先检查全部结构、范围与工作区编号冲突，再整体替换指定工作区，失败不部分写入，不影响另一工作区。

## 私有存储与验证

数据库必须放在应用私有运行目录，不能复制进源代码、公开仓库、OneDrive 镜像或项目产物目录。项目经验可能含用户业务内容。它应进入用户主动下载的认证备份及本机 SQLite 一致性备份；发布代码包不能包含数据库、WAL、SHM或经验备份内容。日志只记录泛化错误，不打印请求、经验正文或材料。

实现前的只读检查只使用了少量项目元数据与代表性对话。大量轮次仍在等用户补充，说明未确认的数值和口径不能作为自动事实。本文和测试仅含合成示例，不包含真实项目、业务数字或私有目录。

聚焦测试命令：`python -m unittest tests.test_project_experience -q`。覆盖持久偏好与单次候选、全终态记录、动作回执、执行阶段防推理泄漏、工作区/项目/用途隔离、开关、用户修订/删除、来源真实性与变更、测算口径、上下文预算、重启及原子恢复。
