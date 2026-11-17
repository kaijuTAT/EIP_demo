# EIP_demo · 线缆试验本地质控助手

在可访问 EIP 的工作站上，按工单读取线缆局放、耐压试验的步骤和曲线，输出可追溯的人工复核清单。工具提供本地仿真服务。管理页默认监听 `127.0.0.1:8765`，数据库、截图和报表写在本机。

## 本地演示

使用 Python 3.10 或更新版本。安装运行依赖及 Chromium。

```bash
pip install -r requirements.txt
playwright install chromium
python run_demo.py
python run_collect.py --target mock
python -m app.main
```

`run_demo.py` 使用固定的脱敏样本演示规则。`run_collect.py --target mock` 自动启动本机仿真服务，实际驱动浏览器读取三个工单。命令行摘要给出采集任务 ID、已识别试验、异常线索和待复核数。管理页支持工单采集、阅页诊断、样例演示和收货台账核对。

## 接入现场页面

1. 在 `config/targets.yaml` 配置现场业务入口。确认该账号可阅的页面、允许留存的字段及本机证据范围。由工作人员完成登录并进入生产试验列表。
2. 运行 `python run_probe.py --target real_eip --minutes 10` 并人工浏览样本详情。诊断在浏览器所属线程处理延迟响应及新标签页。查看 `evidence/probe_*/response_types.json` 中同源 JSON 路径、状态及次数。真实目标的 `probe_url_patterns` 初始为空，配置获准留存的业务路径后，相应正文才写入 `raw/`。空候选需要与页面加载及人工读数对照。
3. 结构化数据适配在 `config/api_map.yaml`。可映射工单、试验标识、实际上传时间、步骤、时间/kV/pC 及列表总数。确认业务接口的授权及映射后，在目标配置设置 `capture_api: true`。同一详情若返回多个试验，需在列表映射中提供 `trial_key` 来选择对应记录。
4. 在 `config/page_templates.yaml` 填写列表行、行内详情链接 `detail_link`、下一页、详情字段及步骤的选择器。对于曲线悬浮框，填写 `tooltip` 和绘图区 `plot_area`。直接可读的文字优先使用 DOM，图形化悬浮框按时间/电压/局放量做本地 OCR。悬浮采样保留逐点截图。低置信度 OCR 候选与截图可在规则证据中人工核对，候选数值不参加自动判定。OCR 模型及运行组件应预装在工作站。
5. 运行 `python run_collect.py --target real_eip --limit 5`。真实目标默认显示浏览器，等待已登录的工单列表。列表按配置翻页、去重、限速。已知列表总数与取得的范围不符时提示检查翻页，缺少详情或试验标识的工单进入采集不完整清单。

试点可先对照 5–10 个详情页的时间、电压和局放量，再用已告警、未告警以及缺测工单做独立复核。

## 判定与证据

`config/rules.yaml` 定义步骤文字、等级参数、判定容差及窗口内允许的采样间隔。随附数值属于试点配置，发布前应核对适用的品类和业务规范。

- R-PD-01 按步骤时间排序校核流程。R-PD-02/05 在局放升压段、耐压段分别检查峰值。
- R-PD-03 按有效时间戳计算连续平台时长。离开平台、读数缺失或过长的采样间隔均中断连续段。
- R-PD-04 核对降压完成时刻附近的读数，并指出随后采值时段的越界点。R-PD-06 在实际采值窗口检查局放量，R-PD-07 对比背景与采值窗口。混合越限、证据缺失或窗口覆盖不足会进入复核。R-PD-08 检查采样缺口与重复读数。
- 报表区分通过、异常线索和待复核，并列出工单、成品、试验、规则版本、对应时段与证据。每条判定的 JSON 包含本规则选中的采样点及其来源。HTML 中的复核链接可打开本机管理服务，保存复核状态和意见。导出的报表是生成当时的记录，复核页显示当前意见。

`evidence/collect_*/trial_*/` 按详情归档原始业务响应、截图与规则证据，`reports/out/` 存放 Excel 和 HTML。SQLite 旧库首次打开时自动增列，历史人工意见保留。

## 收货台账核对

R-TS-01 从收货清单逐行核对，可使用 UTF-8 CSV 或 XLSX 的第一张工作表。两侧需使用一致的工单号和成品编号，按批次核对时两侧都使用批次号。带前导零的编号应保存为文本。

| 台账 | 字段示例 |
| --- | --- |
| 收货或入库 | 工单号、成品编号（或批次号）、收货时间（或入库时间） |
| EIP 上传记录 | 工单号、成品编号（或批次号）、上传时间 |

```bash
python run_receipts.py --receipts receipts.xlsx --task-id 1
python run_receipts.py --receipts receipts.csv --uploads uploads.xlsx
python run_receipts.py --receipts receipts.csv --uploads uploads.xlsx --complete-through "2026-11-17 18:00:00"
```

只有完整覆盖同一业务范围的上传台账，才应填写 `--complete-through`。缺少匹配且覆盖至收货时点，可输出疑似漏传。抽样采集无匹配只列为待复核。核验晚传使用平台实际上传时间。日期精度不足、上传时间缺失或冲突均标出原因。

## 回归测试

```bash
pip install pytest
python -m pytest -q tests
```

测试使用本机仿真页面和临时数据库，覆盖延迟响应、列表翻页、悬浮信息、三态判定、逐条证据、复核保存、收货匹配及旧库兼容。
