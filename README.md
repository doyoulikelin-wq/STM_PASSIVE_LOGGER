# STM Passive Logger and Annotation UI

STM/Nanonis 实验的便携式被动记录与离线标注工具。

它面向两类使用者：

- 实验员：双击 `.bat` 即可自检、健康检查、启动 logger、打开标注 UI。
- 开发者：可直接从源码运行 CLI、测试数据写入、维护标注 schema 和导入流程。

## 当前能力

- 只读连接 Nanonis TCP Programming Interface，读取 Bias / Current / Z / feedback / scan status。
- 自动检查端口 `6501 -> 6502 -> 6503 -> 6504 -> 65004`，只有 Bias / Current 等核心读数能返回的端口才会被选中。
- 自动读取 Nanonis 的 Session 保存目录，监听新保存的 `.sxm`。
- 捕获 `.sxm` 后写入 SQLite + JSONL，生成 PNG 预览，并把原始 `.sxm` 归档到 `data/raw_sxm/<session_id>/`。
- 离线标注 UI 支持多标注者、review、session 筛选、session 总览、标签分布统计。
- 标注 UI 可从归档 `.sxm` 动态渲染不同 channel / forward-backward / difference 视图，并调节大小、LUT、contrast、colorbar、flip 和 plane subtraction。
- 标注 UI 可导入历史 Nanonis session：既支持浏览器选择文件夹，也支持直接输入本机路径如 `D:\STM\S2`。
- 便携包自带 Python 3.11 和依赖，实验室电脑不需要 pip、不需要管理员权限、不需要联网。

## 快速使用

实验员优先使用便携包里的 `.bat`：

| 文件 | 作用 |
| --- | --- |
| `自检.bat` | 检查内置 Python、依赖、工具代码、标注 UI 文件是否完整。 |
| `健康检查.bat` | 探测 Nanonis TCP、端口、session 路径和一次信号快照。 |
| `启动logger.bat` | 开始一次被动记录 session。 |
| `打开标注UI.bat` | 打开离线标注网页 `http://127.0.0.1:8765`。 |
| `查看数据.bat` | 打开当前包里的 `data\` 数据目录。 |

完整实验员手册见 [实验员说明.md](实验员说明.md)。

每个 UI 按钮、筛选项、标注选项和导入选项的含义见 [UI_BUTTONS_AND_OPTIONS.md](UI_BUTTONS_AND_OPTIONS.md)。

源码运行与数据库/API 说明见 [USAGE.md](USAGE.md)。

## 数据目录

默认所有数据都在便携包目录的 `data\`：

```text
data/
  session.sqlite                  SQLite 主数据库
  sessions/<session_id>/           events/signals/scans JSONL 备份
  previews/<session_id>/           PNG 预览图
  raw_sxm/<session_id>/            归档后的原始 .sxm
```

标注 UI 只显示已经写进 `session.sqlite` 的 `scans` 行。手动把 `.sxm` 复制到 `data\` 里不会显示；需要由 logger 自动捕获，或在标注 UI 中使用“导入历史 session”。

## 安全承诺

本工具只读 Nanonis，不会给仪器下控制命令。

- `NanonisReadOnlyClient` 只暴露读取方法。
- raw protocol 也走白名单。
- 任何 `*_Set`、`Pulse`、`Approach`、`Motor`、`TipShaper`、`Withdraw`、`AutoApproach` 等写操作都会被拒绝。

## 开发者快速启动

```powershell
cd D:\STM\logger
python -m pip install -e .[dev]
python -m pytest -q
python -m stm_experimenter_agent.cli probe
python -m stm_experimenter_agent.cli annotate-serve --data-root .\data --port 8765
```

当前测试覆盖 Nanonis raw protocol、只读 client、SQLite/JSONL writer、`.sxm` parser、标注 UI store/API、历史 `.sxm` 导入和路径迁移兜底。