# UI Buttons and Options

本文档解释当前便携包和标注网页中的每个按钮、输入框、筛选项和标注选项。

## 1. 便携包里的 .bat

| 文件 | 什么时候用 | 做什么 | 正常结果 |
| --- | --- | --- | --- |
| `自检.bat` | 第一次解压后，或怀疑包不完整时 | 检查内置 Python、依赖、`stm_experimenter_agent`、标注 UI HTML、标签 schema | 看到 `自检通过!` |
| `健康检查.bat` | 开实验前，logger 还没启动时 | 连接 Nanonis TCP，检查端口、session 路径、Bias/Current 等核心读数 | JSON 最外层 `ok: true` |
| `启动logger.bat` | 正式实验记录时 | 询问 operator/sample/tip/material/notes，然后启动被动 logger | 看到 `session started` |
| `打开标注UI.bat` | 实验后离线标注时 | 在本机启动网页服务并打开 `http://127.0.0.1:8765` | 浏览器出现标注界面 |
| `查看数据.bat` | 想看数据库/JSONL/预览图时 | 打开便携包里的 `data\` 文件夹 | 看到 `session.sqlite` 等文件 |

注意：如果 logger 已经在运行，`健康检查.bat` 可能因为 Nanonis TCP 连接被占用而失败，这是正常情况。以 logger 窗口中的 `session started`、`connected to Nanonis`、`signals batch ... written` 为准。

## 2. 标注 UI 顶部栏

| 控件 | 类型 | 作用 |
| --- | --- | --- |
| `标注者` | 文本框 | 必填。写入 `labels.annotator`，也作为 review 时的 `reviewer`。浏览器会记住上次输入。 |
| `Session` | 下拉框 | 选择所有 sessions，或只看某一次实验。每项显示已标图数/总图数和 sample。 |
| `显示` | 下拉框 | 控制左侧扫描列表。`仅我未标` 只看当前标注者没标过的图；`仅我已标` 只看当前标注者标过的图；`全部` 显示全部。 |
| `刷新` | 按钮 | 重新读取统计、session 总览和扫描列表。导入历史数据或别人刚标完后可点。 |
| 顶部统计文字 | 状态显示 | 显示总图数、我已标、未标、待 review 数。 |

## 3. 导入历史 session 面板

在左侧展开 `导入历史 session`。这个面板用于把已有 Nanonis `.sxm` 文件导入当前 `data\session.sqlite`，并生成 PNG 预览。

| 控件 | 类型 | 作用 |
| --- | --- | --- |
| `session_id` | 文本框 | 导入后新建/复用的 session 名。建议写成日期+目录+样品+针尖，例如 `20260525_S2_BP5_tip01`。不填时选择文件夹会自动生成。 |
| `sample` | 文本框 | 样品编号，写入 `sessions.sample_id`。 |
| `tip` | 文本框 | 针尖编号，写入 `sessions.tip_id`。 |
| `material` | 文本框 | 材料说明，写入 `sessions.material`。 |
| `operator` | 文本框 | 操作者，写入 `sessions.operator`。默认可用顶部标注者。 |
| 路径框 | 文本框 | 输入本机路径，例如 `D:\STM\S2`。用于服务端直接读取该目录或单个 `.sxm` 文件。 |
| `选择文件夹` | 按钮 | 浏览器选择包含 `.sxm` 的文件夹，然后逐个上传到本地服务导入。适合不想手打路径的实验员。 |
| `导入路径` | 按钮 | 读取路径框中的本机目录/文件。适合文件很多时使用，速度更快。 |
| `子目录` | 复选框 | 勾选后，`导入路径` 会递归查找子目录中的 `.sxm`。 |
| 进度条 | 状态显示 | 文件夹上传时显示当前导入进度。 |
| 状态文字 | 状态显示 | 显示导入成功张数和第一个错误。 |

导入成功后：

- 原始 `.sxm` 会复制到 `data\raw_sxm\<session_id>\`。
- PNG 预览会生成到 `data\previews\<session_id>\`。
- `scans` 表会新增记录。
- Session 下拉会自动切到导入的 session。

## 4. 左侧 session 总览

| 项目 | 含义 |
| --- | --- |
| `总图数` | 当前 session 或所有 sessions 中的扫描数。 |
| `已标图数` | 至少有一个标注者标过的扫描数。 |
| `我已标` | 当前顶部标注者标过的扫描数。 |
| `标签总数` | 所有标注记录总数。多人标同一张会计为多条标签。 |
| `待 review` | `review_status` 为空的标签数。 |
| 标签分布 | 按衬底、薄膜、分子、image_quality、tip_state、surface_quality、research_value_label、next_action、artifact_tags、review_status 统计。 |

## 5. 左侧扫描列表

每一行是一张扫描。

| 显示内容 | 含义 |
| --- | --- |
| `scan_id` | 原始文件名加 hash，避免重名。 |
| 时间 | 捕获或导入时间。 |
| `bias` | `.sxm` 解析出的 bias。 |
| 像素数 | `pixels_x × pixels_y`。 |
| `共 N 个标签` | 当前扫描已有多少条标注。 |
| 绿色左边框 | 当前标注者已经标过。 |
| 橙色左边框 | 其他人标过，但当前标注者还没标。 |

点击一行会在右侧打开详情。

## 6. 右侧扫描详情

| 区域 | 说明 |
| --- | --- |
| 元数据条 | 显示 session、sample、tip、operator、捕获时间、bias、setpoint、像素、范围、`.sxm` 路径。 |
| PNG 预览 | 显示 `preview_path` 对应的图。优先 Z forward，其次 Current / LI。 |
| 标注表单 | 当前标注者对这张图的标签。 |
| 其他标注者 | 展示别人对同一张图的标签，并可 review。 |
| 我的当前标签 | 当前标注者已保存过时显示自己的历史标签。 |

## 7. 标注字段和选项

### 7.1 可手填并自动带到下一张

| 字段 | 类型 | 默认选项 | 行为 |
| --- | --- | --- | --- |
| `衬底/基底` (`substrate`) | 可下拉也可手填 | `HOPG`, `Au(111)`, `Ag(111)`, `Cu(111)`, `Si(111)`, `SiO2/Si`, `mica`, `graphene`, `hBN`, `unknown` | 保存后会带到下一张未标图。 |
| `薄膜` (`thin_film`) | 可下拉也可手填 | `none`, `Bi2Se3`, `BP`, `MoS2`, `WSe2`, `hBN`, `graphene`, `organic_film`, `unknown` | 保存后会带到下一张未标图。 |
| `分子` (`molecule`) | 可下拉也可手填 | `none`, `unknown` | 保存后会带到下一张未标图。 |

### 7.2 单选/等级标签

| 字段 | 选项 | 建议含义 |
| --- | --- | --- |
| `image_quality` | `excellent`, `usable`, `questionable`, `unusable` | 图像质量：很好、可用、有疑问、不可用。 |
| `tip_state` | `good_tip`, `dirty_tip`, `double_tip`, `blunt_tip`, `unstable_tip`, `crashed_tip_suspected`, `unknown` | 针尖状态。 |
| `surface_quality` | `flat_terrace`, `tilted_plane`, `rough_surface`, `contaminated`, `step_edge_dense`, `unstable_area`, `unknown` | 表面状态。 |
| `research_value_label` | `high_value`, `medium_value`, `low_value`, `not_relevant`, `unknown` | 研究价值粗分类。 |
| `next_action` | `fine_scan_roi`, `zoom_in`, `move_region`, `adjust_params`, `tip_check`, `recommend_repair`, `run_sts`, `run_cits`, `run_didv_mapping`, `stop`, `ask_human` | 建议下一步操作。 |

### 7.3 多选标签

`artifact_tags` 可多选：

- `stripe_noise`：条纹噪声。
- `drift`：漂移。
- `blur`：模糊。
- `line_jump`：行跳变。
- `double_image`：双像。
- `feedback_instability`：反馈不稳定。
- `saturation`：信号饱和。
- `low_contrast`：对比度低。
- `contamination`：污染。

### 7.4 数值和文本

| 字段 | 范围/类型 | 说明 |
| --- | --- | --- |
| `research_value_score` | `0.0` 到 `1.0` | 研究价值分数。 |
| `confidence` | `0.0` 到 `1.0` | 标注者对自己判断的信心。 |
| `reason_text` | 长文本 | 判断依据。 |
| `annotator_notes` | 长文本 | 自由备注，review 时可见。 |

## 8. 标注按钮

| 按钮 | 作用 |
| --- | --- |
| `保存我的标签` | 保存当前表单，不切换扫描。若同一标注者之前标过同一张，会覆盖旧记录并更新 `updated_ts`。 |
| `保存并下一张 ->` | 保存当前表单，然后跳到扫描列表中的下一张。 |

保存规则：

- 主键是 `(scan_id, annotator)`。
- 同一个人重标同一张 = 覆盖。
- 不同人标同一张 = 多条标签共存。
- `substrate`、`thin_film`、`molecule` 会保存在浏览器 localStorage，用作下一张图的默认值。

## 9. Review 区域

其他标注者的标签卡片下方有 review 表单。

| 控件 | 作用 |
| --- | --- |
| review 状态下拉框 | 选择 `accept`、`dispute` 或 `need_redo`。 |
| review 评论 | 可选文本，写入 `review_comment`。 |
| `提交 review` | 保存 review。reviewer 是顶部 `标注者` 输入框中的名字。 |

Review 状态含义：

- `accept`：认可这条标签。
- `dispute`：不同意或有分歧。
- `need_redo`：需要重新标注或重新采图。

## 10. 常见判断

- `version.ok` 为 false 但最外层 `ok` 为 true：通常可继续用。以 `port_probe_results.core_probe` 中的 `Bias_Get` 和 `Current_Get` 为准。
- `.sxm` 复制到 `data\` 后 UI 看不到：正常。UI 不扫描散落文件，只读数据库和 PNG 预览。请用 logger 捕获或导入历史 session。
- 导入后没有预览：该 `.sxm` 可能没有可渲染通道，或解析/渲染失败；看导入状态和终端输出。