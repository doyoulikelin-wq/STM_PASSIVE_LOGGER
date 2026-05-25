# Source, CLI, Data and API Usage

本文档面向开发者和需要查看 GitHub 源码的人，说明当前源码结构、CLI、数据目录、SQLite 表、标注 API 和测试方式。

实验员操作说明见 [实验员说明.md](实验员说明.md)。

UI 按钮和选项说明见 [UI_BUTTONS_AND_OPTIONS.md](UI_BUTTONS_AND_OPTIONS.md)。

## 1. 源码结构

```text
stm_experimenter_agent/
  cli.py                         CLI 入口：probe/start/annotate-serve/annotate-stats
  config/
    logger.yaml                  数据目录、轮询频率、预览配置
    nanonis_ports.yaml           Nanonis host/port/fallback 配置
    label_schema.yaml            标注 UI 字段和选项
  nanonis_driver/
    client.py                    只读 Nanonis wrapper + 端口 core probe
    raw_protocol.py              raw TCP helper，只用于白名单读命令
  data_collection/
    dataset_writer.py            SQLite schema、JSONL 写入、迁移
    scan_capture.py              监听新 .sxm、归档原始文件、生成 preview
    sxm_parser.py                Nanonis .sxm header/data parser
    sxm_archive.py               data_root 下的 raw_sxm 归档工具
    sxm_importer.py              历史 .sxm 路径/上传导入工具
    preview.py                   PNG 预览渲染
  annotation/
    server.py                    stdlib HTTP server 和 JSON API
    store.py                     标注查询、统计、session overview、review
    index.html                   单文件离线标注 UI
tests/                           离线测试，不需要 Nanonis 在线
```

## 2. 安装和测试

源码开发环境：

```powershell
cd D:\STM\logger
python -m pip install -e .[dev]
python -m pytest -q
```

便携包不需要安装 Python。实验员使用 `stm_logger_v0.11_with_python` 里的 `.bat` 即可。

## 3. CLI 命令

### 3.1 健康检查

```powershell
python -m stm_experimenter_agent.cli probe
```

输出字段：

| 字段 | 含义 |
| --- | --- |
| `connected_port` | 实际选中的 Nanonis TCP 端口。 |
| `port_probe_results` | 每个端口的 core probe 结果。`Bias_Get` 和 `Current_Get` 都 ok 才认为能交互数据。 |
| `version` | `Util.VersionGet` 结果。部分 Nanonis 会超时，若最外层 `ok` 为 true 通常可忽略。 |
| `session_path` | Nanonis 当前保存 `.sxm` 的目录，例如 `D:\STM\S2`。 |
| `sample_snapshot` | 一次 Bias / Current / Z / feedback / scan status 快照。 |
| `ok` | 最外层健康状态。 |

如果 logger 已经运行，健康检查可能因为 TCP 连接被占用而失败。此时以 logger 窗口中的 `session started` 和信号写入日志为准。

### 3.2 启动 logger

```powershell
python -m stm_experimenter_agent.cli start `
  --operator myl `
  --sample BP5-001 `
  --tip tip01 `
  --material Bi2Se3 `
  --notes first_run `
  --data-root D:\STM\logger\data
```

可选参数：

| 参数 | 说明 |
| --- | --- |
| `--host` | Nanonis host，默认配置为 `127.0.0.1`。 |
| `--port` | 指定主端口；不填时从 `nanonis_ports.yaml` 读取。 |
| `--watch-dir` | 手动指定 `.sxm` 监听目录；不填时尝试从 Nanonis `SessionPathGet` 自动读取。 |
| `--poll-hz` | 信号轮询频率，默认 `1.0`。 |
| `--data-root` | 数据根目录，便携包默认是自身目录下的 `data\`。 |
| `--operator` | 操作者。 |
| `--sample` | 样品编号。 |
| `--tip` | 针尖编号。 |
| `--material` | 材料说明。 |
| `--notes` | 备注。 |

Ctrl+C 会安全退出，写入 `session_end` 事件并 flush 缓存。

### 3.3 启动标注 UI

```powershell
python -m stm_experimenter_agent.cli annotate-serve `
  --data-root D:\STM\logger\data `
  --port 8765
```

参数：

| 参数 | 说明 |
| --- | --- |
| `--data-root` | 已有 logger 数据目录。必须包含 `session.sqlite`。 |
| `--host` | 默认 `127.0.0.1`。需要局域网访问时可改为 `0.0.0.0`，注意防火墙。 |
| `--port` | 默认 `8765`。 |
| `--no-browser` | 不自动打开浏览器。 |

### 3.4 标注统计

```powershell
python -m stm_experimenter_agent.cli annotate-stats --data-root D:\STM\logger\data --annotator myl
```

## 4. Nanonis 端口逻辑

配置文件：[stm_experimenter_agent/config/nanonis_ports.yaml](stm_experimenter_agent/config/nanonis_ports.yaml)

当前默认：

- host: `127.0.0.1`
- primary_port: `6501`
- fallback_ports: `6502`, `6503`, `6504`, `65004`
- timeout: `5.0` 秒

端口选择不再以 TCP connect 成功为准，而是要求至少 `Bias_Get` 和 `Current_Get` 能返回。`Util_SessionPathGet` 会作为诊断项记录。

## 5. 数据目录和路径策略

```text
data/
  session.sqlite
  sessions/<session_id>/events.jsonl
  sessions/<session_id>/signals.jsonl
  sessions/<session_id>/scans.jsonl
  previews/<session_id>/<scan_id>_<channel>_fwd.png
  raw_sxm/<session_id>/<scan_id>.sxm
```

路径策略：

- Nanonis 保存目录可以是 `D:\STM\S2`。
- logger/标注 data root 可以是 `D:\STM\stm_logger_v0.11_with_python\data`。
- 新捕获或历史导入的 `.sxm` 会复制进 `data/raw_sxm/<session_id>/`。
- `scans.sxm_path` 和 `scans.preview_path` 优先存相对 data root 的路径，便于整体拷走。
- 旧数据库如果存了绝对 `preview_path`，UI 会尝试按当前 data root 下的 `previews/...` 兜底解析。

## 6. SQLite 表

| 表 | 一行代表 | 关键字段 |
| --- | --- | --- |
| `sessions` | 一次实验或一次历史导入 session | `session_id`, `start_ts`, `end_ts`, `operator`, `sample_id`, `tip_id`, `material`, `notes`, `instrument` |
| `scans` | 一张 `.sxm` 扫描 | `scan_id`, `session_id`, `captured_ts`, `sxm_path`, `preview_path`, `bias_V`, `setpoint`, `pixels_x/y`, `range_x/y_m`, `channels`, `metadata` |
| `signals` | 一次信号轮询 | `session_id`, `ts`, `bias_V`, `current_A`, `z_m`, `z_ctrl_on`, `scan_status`, `errors` |
| `events` | 一个离散事件 | `session_id`, `ts`, `kind`, `payload` |
| `labels` | 一个人对一张扫描的标注 | `(scan_id, annotator)`, `substrate`, `thin_film`, `molecule`, `image_quality`, `tip_state`, `surface_quality`, `artifact_tags`, `research_value_label`, `research_value_score`, `next_action`, `confidence`, `reason_text`, `annotator_notes`, `review_status`, `review_comment`, `reviewer`, `reviewed_ts` |

常用查询：

```sql
SELECT session_id, operator, sample_id, tip_id, start_ts
FROM sessions ORDER BY start_ts DESC LIMIT 10;

SELECT scan_id, captured_ts, sxm_path, preview_path, bias_V, pixels_x, pixels_y
FROM scans WHERE session_id = '20260525_S2_BP5_tip01'
ORDER BY captured_ts;

SELECT scan_id, annotator, image_quality, tip_state, review_status
FROM labels ORDER BY updated_ts DESC LIMIT 20;
```

## 7. 标注 API

标注服务由 `annotation/server.py` 提供，使用 Python 标准库 `http.server`，没有 Flask/FastAPI 依赖。

GET endpoints：

| Endpoint | 说明 |
| --- | --- |
| `/` 或 `/index.html` | 返回标注 UI。 |
| `/api/schema` | 返回 `label_schema.yaml`。 |
| `/api/sessions` | 返回 sessions 列表和每个 session 的总图数/已标数。 |
| `/api/stats?annotator=myl` | 返回全局标注进度。 |
| `/api/session-overview?session_id=...&annotator=...` | 返回当前 session 总览和标签分布。 |
| `/api/scans?annotator=...&mode=unlabeled&session_id=...&limit=300` | 返回扫描列表。`mode` 可为 `unlabeled`, `labeled`, `all`。 |
| `/api/scan/<scan_id>` | 返回单张扫描详情和所有标签。 |
| `/preview/<scan_id>.png` | 返回 PNG 预览。 |

POST endpoints：

| Endpoint | Body | 说明 |
| --- | --- | --- |
| `/api/label` | JSON: `scan_id`, `annotator`, `fields` | 插入或更新当前标注者的标签。 |
| `/api/review` | JSON: `scan_id`, `annotator`, `reviewer`, `status`, `comment` | review 某个标注者的标签。 |
| `/api/import-path` | JSON: `path`, `session_id`, `session_meta`, `recursive` | 从本机路径导入历史 `.sxm`。 |
| `/api/import-upload` | multipart: `file`, `session_id`, metadata | 浏览器文件夹选择后逐个上传导入 `.sxm`。 |

## 8. 标签 schema

配置文件：[stm_experimenter_agent/config/label_schema.yaml](stm_experimenter_agent/config/label_schema.yaml)

字段类型：

- `combo`：下拉建议 + 可手填。目前用于 `substrate`, `thin_film`, `molecule`。
- `single`：单选。
- `ordinal`：有序单选。
- `multi`：多选 checkbox。
- `float`：数值输入。
- `text`：长文本。

`substrate`、`thin_film`、`molecule` 带 `carry_over: true`，保存后会自动作为下一张图的默认值。

## 9. 历史 .sxm 导入

源码入口：[stm_experimenter_agent/data_collection/sxm_importer.py](stm_experimenter_agent/data_collection/sxm_importer.py)

主要流程：

1. 找到 `.sxm` 文件。
2. 复制到 `data/raw_sxm/<session_id>/`。
3. 用 `sxm_parser.load_sxm` 解析 metadata 和通道数据。
4. 渲染 PNG 到 `data/previews/<session_id>/`。
5. 写入 `sessions`、`scans`、`events`。

UI 中的 `选择文件夹` 走 `/api/import-upload`，`导入路径` 走 `/api/import-path`。

## 10. 测试重点

```powershell
python -m pytest -q
```

测试覆盖：

- raw TCP header/body decode。
- 只读 client 禁止写操作。
- 端口连接后必须 core probe 成功。
- SQLite/JSONL 写入和 labels schema 迁移。
- `.sxm` header/data parser。
- 标注 store、HTTP API、review、session overview。
- 历史 `.sxm` 路径导入和 upload 导入。
- live scan capture 把原始 `.sxm` 归档到 data root。
- 移动 data root 后旧 absolute preview path 的兜底解析。

## 11. 已知注意事项

- Windows PowerShell 不支持 Bash here-doc。需要临时 Python 脚本时请用 `python -c`、PowerShell here-string 或真正的临时文件。
- Windows `.bat` 在 `chcp 65001` 下可能误解析含中文的 `.bat` 文件名片段，因此自检提示中避免直接 echo `打开标注UI.bat`。
- 不要手动把 `.sxm` 放进 `data\` 后期待 UI 显示；必须通过 logger 捕获或历史导入流程写入数据库。