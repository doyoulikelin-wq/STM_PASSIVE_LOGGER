# Passive Logger — 使用说明 (V0)

本目录是 STM Experimenter Agent 的 **被动数据采集器**。它在实验过程中只读 Nanonis、监听保存目录、把所有信号 / 扫描 / 事件统一写入 SQLite + JSONL。
**它从不向硬件下任何写命令**，所以可以与实验员的正常操作并行运行。

---

## 1. 安装

```powershell
cd D:\STM\logger
python -m pip install -e .
```

之后包名 `stm_experimenter_agent` 即可被 Python 引用，CLI 入口是 `python -m stm_experimenter_agent.cli`。

---

## 2. 输入 —— 它需要什么、从哪里拿

| 输入项 | 来源 | 谁提供 | 是否必填 |
| --- | --- | --- | --- |
| **Nanonis TCP Programming Interface** 已经打开 | Nanonis Mimea V5e 主界面 `Utilities → TCP Programming Interface` | 实验员 | 必须，否则信号轮询会报错（采集仍会继续，只是 signals 表的字段是 NULL+错误说明） |
| TCP host / port | 默认尝试 `127.0.0.1:6501`，失败自动 fallback 到 `6502 / 6503`（见 [`config/nanonis_ports.yaml`](stm_experimenter_agent/config/nanonis_ports.yaml)）。可用 `--host / --port` 覆盖 | 实验员 | 默认即可 |
| Nanonis 保存目录 | **优先自动从 Nanonis 自身读取**（`Util.SessionPathGet`）；若 Nanonis 未配置，可用 `--watch-dir` 手动指定 | 实验员 / 自动 | 选填；都没有则只采信号、不索引扫描 |
| 会话元数据：`operator / sample / tip / material / notes` | 实验员现场输入（CLI 参数） | 实验员 | 至少填 `--operator` 和 `--sample`，便于后续筛选 |
| 轮询频率 `--poll-hz` | 命令行 | 实验员 | 默认 `1.0` Hz；机器忙时可降到 `0.5` |
| 数据根目录 `--data-root` | 命令行 | 实验员 | 默认当前工作目录下的 `data/` |

> 采集器 **不** 读取 Nanonis 的实时扫描帧（避免占用控制带宽），它只在 `.sxm` 文件落盘后解析。所以"输入图像"的来源 = Nanonis 自己保存的 `.sxm` 文件。

---

## 3. 输出 —— 写什么、放在哪里

所有产物都落在 `--data-root` 指定的目录（默认 `./data/`）：

```
<data_root>/
├── session.sqlite                ← 索引库（可用 DB Browser / sqlite3 打开）
│   ├── sessions                  会话元数据 + 仪器版本
│   ├── scans                     每个 .sxm 一行，含扫描参数和 preview 路径
│   ├── signals                   每次轮询一行 Bias/Current/Z/feedback 状态
│   └── events                    session_start / scan_captured / session_end / 自定义事件
│
├── sessions/<session_id>/
│   ├── events.jsonl              事件追加日志（可 grep）
│   ├── signals.jsonl             信号追加日志（与 SQLite 双写，互为备份）
│   └── scans.jsonl               每个新 .sxm 的元数据快照
│
└── previews/<session_id>/
    └── <scan_id>_<channel>_fwd.png   扫描首选通道（Z → Current → LI_Demod_1_X）的 PNG 预览
```

`session_id` 格式： `YYYYmmdd_HHMMSS_<sample>_<tip>`，例如 `20260519_213045_sampleA_tip03`。

`scan_id` = 原始 `.sxm` 文件名 + 8 位 hash（防止重名）。

### SQLite 表关系速查

```sql
-- 看本次 session 已捕获多少张扫描
SELECT scan_id, captured_ts, bias_V, pixels_x, preview_path
FROM scans WHERE session_id = '20260519_213045_sampleA_tip03'
ORDER BY captured_ts;

-- 看最近 60 秒的信号
SELECT ts, bias_V, current_A, z_m, scan_status
FROM signals WHERE session_id = ? AND ts > strftime('%s','now') - 60;

-- 看所有事件
SELECT ts, kind, payload FROM events WHERE session_id = ? ORDER BY ts;
```

---

## 4. 启动命令

```powershell
# 健康检查：探测端口 + 版本 + Nanonis 自报的 session 路径 + 一次信号快照
python -m stm_experimenter_agent.cli probe

# 启动一次被动采集 session（--watch-dir 已可省略，会从 Nanonis 自动发现）
python -m stm_experimenter_agent.cli start `
    --operator linye `
    --sample sampleA `
    --tip tip03 `
    --material "Bi2Se3" `
    --poll-hz 1.0 `
    --data-root D:\STM\logger\data
```

`probe` 的典型输出（注意 `connected_port` 与 `session_path`）：

```json
{
  "connected_port": 6502,
  "version": { "ok": true, "app": "..." },
  "session_path": "D:\\STM\\S2",
  "sample_snapshot": { "bias_V": 2.0, "current_A": -6.5e-11, ... },
  "ok": true
}
```

按 `Ctrl + C` 优雅退出：会停止采集线程、flush 全部缓存、把 `end_ts` 写回 `sessions` 表，并写入 `session_end` 事件（含 signals/scans 计数）。

> **PowerShell 引号提示**：上面示例里的 `linye / sampleA / tip03` 等都是真实值，**不要**把它们写成 `<你的名字>` 这种带尖括号的占位符 —— PowerShell 把 `<` 视为保留运算符会直接报错。任何含空格、中文、`&`、括号的值请用双引号包起来（如 `--material "Bi2Se3 thin film"`）。行尾的反引号 `` ` `` 是续行符，后面不能再有空格。

---

## 5. 行为保证（工程不变量）

1. **零写**：driver 层方法白名单 + raw protocol 命令白名单，任何 `*_Set / Pulse / Approach / Motor / TipShaper / Withdraw` 调用都会抛 `WriteOperationNotAllowed`。
2. **崩溃可恢复**：JSONL 追加 + SQLite WAL，任一端损坏另一端仍可重建。
3. **不阻塞实验员**：信号轮询 / 扫描监听都跑在守护线程，主线程只等 Ctrl+C。
4. **不重灌历史**：scan watcher 启动时会把当前已存在的 `.sxm` 视为基线跳过，只入库启动后新增的文件；这避免反复重启时重复入库。
5. **断网可恢复**：连续 5 次读取失败后主动断开 socket，下次轮询自动重连。
6. **TCP 接口异常**：`Util.VersionGet` 走自实现的 raw 协议，绕开 `nanonis_spm` 的 `bad char in struct format` bug。

---

## 6. 常见问题

| 现象 | 原因 / 处理 |
| --- | --- |
| `probe` 显示 `Could not reach a Nanonis Programming Interface on any of ...` | Nanonis 主程序的 TCP Programming Interface 没打开，或端口不在 `6501/6502/6503/65004`。检查 `File / Settings → Options → TCP Programming Interface` 并把实际端口加到 `config/nanonis_ports.yaml` 的 `fallback_ports`。 |
| `probe` 输出 `connected_port` 不是 6501 | 正常。本机 Nanonis 安装实测 6501 是 LabVIEW VI Server（接受连接但不应答 Programming Interface 命令），真实接口在 6502/6503。logger 会自动 fallback，无需手动配。 |
| `probe` 输出 `session_path: null` | Nanonis 还没设过保存目录。要么在 Nanonis `File → Path` 设一个，要么启动时显式 `--watch-dir`。 |
| `signals` 表里 `errors` 字段非空 | 单个字段读取失败（例如 ZCtrl 未启用），其他字段照常入库。 |
| `scans` 表一直没新行 | `--watch-dir` 路径错了，或 Nanonis 还没保存任何 `.sxm`。 |
| 预览图缺失 | `.sxm` 不含 Z / Current / LI_Demod_1_X 任一通道，或 matplotlib 渲染失败（看终端日志）。 |
| `session.sqlite is locked` | 同时有另一个进程在写。一个 data_root 同时只跑一个 logger。 |

---

## 7. 离线标注 (V1)

思路：**logger 在实验期间一直开着，只负责采；标注是离线任务**，积累一批数据后再请实验员进来干。
标注札记会写回同一个 `session.sqlite` 里的 `labels` 表，不会干扰采集进程。

### 启动标注 UI

```powershell
python -m stm_experimenter_agent.cli annotate-serve `
    --data-root D:\STM\logger\data `
    --port 8765
```

默认会自动打开浏览器访问 `http://127.0.0.1:8765/`；不想自开加 `--no-browser`。
多人同时干可以开多个端口，也可以改 `--host 0.0.0.0` 让同事从带机访问（注意防火墙）。

### UI 使用顺序

1. 顶部 「标注者」 填姓名（**必填**，会以此名义写入 `labels.annotator`；localStorage 会记住）。
2. 默认只列 「我未标」 的扫描（只跟此人相关，别人标过也不影响）。可按 session 筛选。
3. 点左侧扫描→右侧出预览图 + 表单；表单从 [`label_schema.yaml`](stm_experimenter_agent/config/label_schema.yaml) 动态生成。
4. 填完点 「保存」 或 「保存并下一张」；`annotator_notes` 是自由备注，review 时能看到。
5. **Review 机制**：同一张扫描下面会列出「其他标注者」的标签卡片，每张卡片下面有一个小表单可以选 `accept / dispute / need_redo` + 写评论，reviewer = 当前顶部填的名字。提交后会写入 `labels.review_status / review_comment / reviewer / reviewed_ts`。

### 查看进度不起服务

```powershell
python -m stm_experimenter_agent.cli annotate-stats --data-root D:\STM\logger\data --annotator linye
```

### `labels` 表 schema 速查

```sql
-- 某人的标注
SELECT * FROM labels WHERE annotator = 'linye' ORDER BY updated_ts DESC;

-- 未 review 的标签
SELECT scan_id, annotator, image_quality, annotator_notes
FROM labels WHERE review_status IS NULL;

-- 同一张图不同人的分歧
SELECT scan_id, annotator, image_quality, tip_state
FROM labels WHERE scan_id IN (
   SELECT scan_id FROM labels GROUP BY scan_id HAVING COUNT(*) > 1
) ORDER BY scan_id;
```

主键是 `(scan_id, annotator)`：同一人重标同一张 = 覆盖（`updated_ts` 更新）；不同人标同一张 = 两条记录共存。

---

## 8. 下一阶段会用这些输出做什么

- `quality_baseline`：从 `scans` 表读 `sxm_path`，用 `sxm_parser.load_sxm` 计算 roughness / stripe / drift 等指标。
- `session_report`：聚合 `sessions / scans / signals / events / labels` 生成每日 HTML/Markdown 总结。
