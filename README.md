# EIP_demo — 本地智能质控 Agent（试点骨架 v0.1）

> Local agent for EIP cable PD-test anomaly screening — everything runs on one
> machine, no cloud calls. 界面只监听 `127.0.0.1`。

国网浙江 EIP 线缆局放试验异常排查工具链。**数据不出网**：全部组件本地运行，
管理界面只监听 127.0.0.1，不调用任何云端接口。演示数据均为脱敏合成样例。

## 目录

```text
eip-agent/
  run_probe.py          ★ D2 流量验证（试点第一步，先跑这个）
  run_collect.py        ★ T1 接口版采集（配 Mock EIP 可本地全链路联调）
  run_demo.py           ★ 端到端演示（不连 EIP，用录屏样本出报表）
  app/main.py             本地管理界面（Flask，127.0.0.1:8765）
  browser/                Playwright 会话 / T1 接口拦截与适配 / 页面解析 / 采集编排
  mock_eip/               Mock EIP 服务（仿真列表/详情/曲线 JSON 接口，联调用）
  capture/                T3 曲线悬浮框采样（虚拟鼠标 + 真鼠标兜底）
  ocr/                    PaddleOCR 优先、RapidOCR 自动退路的本地 OCR
  rules/                  规则引擎（数值判定只走规则不走大模型）
  storage/                SQLite（eip_agent.db）
  reports/                Excel / HTML 报表输出（reports/out/）
  config/                 rules.yaml 规则参数 + page_templates.yaml 页面模板
  evidence/               截图 / 录屏 / 原始 JSON（按任务编号归档）
  tests/                  回归测试（录屏真实样本）
```

## 快速开始（外网 Windows 机）

```bash
pip install -r requirements.txt
playwright install chromium

# 1) 演示：看报表长什么样（不需要 EIP）
python run_demo.py          # → reports/out/异常报表_样例.xlsx/.html

# 2) T1 接口版全链路联调（Mock EIP，本机即可跑）
python -m mock_eip.server           # 可选：单独启动仿真 EIP（127.0.0.1:8777）
python run_collect.py --target mock # 采集→规则→报表，目标配置见 config/targets.yaml

# 3) 测试：规则/OCR 回归 + 接口版端到端集成
python tests/test_rules.py
python tests/test_ocr_fields.py
python tests/test_eip_api_version.py

# 4) 正式第一步：D2 流量验证（人工登录 EIP，翻 5~10 个局放详情页）
python run_probe.py --target real_eip  # 按回车结束，自动输出候选数据接口清单

# 5) 本地管理界面
python -m app.main          # http://127.0.0.1:8765，提交任务后后台执行
```

## 管理页面任务

打开 `http://127.0.0.1:8765` 后可以选择流量验证、工单采集或规则跑批。

- 选择本地 Mock 目标时，管理页会自动启动 `127.0.0.1:8777` 仿真服务
- 选择真实 EIP 时，在 `config/targets.yaml` 填写 `base_url` 和 `entry_url`
- 首次真实采集选择显示浏览器，人工登录后由任务继续采集
- 任务状态会从 `pending` 变为 `running`、`done` 或 `failed`
- 同一任务的页面截图、录屏、响应清单和原始 JSON 会放在 `evidence/collect_<时间>/`
- HTML 报表中的证据名称可以直接打开，Excel 的证据单元格带超链接

真实 EIP 的接入顺序如下。

1. 在 `config/targets.yaml` 填入真实入口地址。
2. 用 `python run_probe.py` 或管理页的“流量验证”打开浏览器，人工登录并翻看 5 到 10 个局放详情页。
3. 根据 `evidence/probe_<时间>/manifest.jsonl` 和 `raw/` 中的响应，回填 `config/api_map.yaml`。
4. 根据页面实际结构回填 `config/page_templates.yaml`。
5. 用只读账号和少量详情页运行采集，人工复核报表后再扩大范围。

## T1 接口版：适配 EIP 接口（config/api_map.yaml）

真实接口未知也能开发——先用 Mock EIP 打通全链路，D2 拿到真实端点后
**只改配置、不改代码**：

```yaml
endpoints:
  trial_curve:
    url_pattern: "/api/trials/.*/curve"   # ← 换成真实 URL 模式
    items_path: "$.data.points"           # ← 换成真实字段路径
    ts: "$.t"  kv: "$.kv"  pc: "$.pc"
```

可靠性设计：浏览器事件回调存在时序偶发（实测会偶发丢回调），所以拦截层
只负责把每个 JSON 响应**原样落盘**（`evidence/collect_*/manifest.jsonl` + `raw/`），
解析一律在采集结束后**从磁盘确定性重放**（`ApiAdapter.replay_manifest`）——
落盘即真相，重放幂等，网络抖动不丢数据。回调异常不再静默吞掉，记入
`intercept_errors` 随任务摘要输出。

切换真实 EIP 的步骤：① `run_probe.py` 拿到端点与字段结构 → ② 回填
`api_map.yaml` → ③ `run_collect.py --base-url https://<eip> --no-headless`
（人工先登录，脚本只接管会话、只读不写）。

## 三级采集策略

| 级别 | 通道 | 用途 |
| --- | --- | --- |
| T1 | `browser/intercept.py` 接口拦截 | 曲线/列表数据若是 JSON 直接落库（`run_probe.py` 验证） |
| T2 | `browser/pages/` DOM 直读 | 成品编号、试验步骤等文字字段，零 OCR 误差 |
| T3 | `capture/hover_sampler.py` 悬浮框采样 + OCR | 曲线兜底；页面不认虚拟鼠标时用 `desktop_fallback.py` 真鼠标 |

原则：能读文字不截图、能接口不 OCR、能虚拟鼠标不动真鼠标。

## 内置规则（config/rules.yaml，业务可改）

- R-PD-01 步骤链完整性（采集→背景值→2U₀→维持10s→降压→采局放值→耐压→完成）
- R-PD-02/04/05 升压 52kV / 降压 44.98kV / 耐压 117kV 目标偏差（26/35kV 等级）
- R-PD-03 平台维持时长 ≥10s
- R-PD-06 稳态局放量超限（默认 10pC，按品类标准配置）
- R-PD-07 背景噪声底异常
- R-PD-08 曲线死点 / 采样缺口
- R-TS-01 上传时效（待物资收货数据接入后启用）

改完参数把 `rules.yaml` 里的 `version` 加一位，报表会带规则版本。

## 纪律红线（已写进代码）

1. 只读不写：不点击"采集/提交"类操作按钮
2. 翻页限速 ≥2 秒（`page_templates.yaml: rate_limit_seconds`）
3. 凭据不落盘：人工登录，`evidence/browser_profile/` 只存登录态
4. 页面模板对不上即抛 `TemplateNotReady` 暂停转人工，不写脏数据
5. pyautogui 兜底开着 FAILSAFE：鼠标甩到屏幕左上角紧急中止

## D2 之后要回填的配置

`config/page_templates.yaml` 里所有 `TODO(D2)`：
- 列表页行选择器 / 翻页按钮
- 详情页字段选择器 / 步骤时间线选择器
- 曲线容器 / 悬浮框选择器 / 采样坐标范围
