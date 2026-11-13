# 工具调用（Tool Calling）能力实测报告

> 日期：2026-11-13
> 范围：doubao2api 接口层对 OpenAI 兼容 `tools` / `tool_calls` 的支持情况与可靠性
> 性质：只读核查 + 真实请求测试；验证过程未修改被测源码，另在 `tests/` 下补充了带鉴权的复现运行器

## 一、结论先行

doubao2api 在**接口层确实支持** OpenAI 格式的工具调用（Function Calling / Tool Use）：调用方可以传入 `tools`，并拿到标准结构的 `tool_calls`（非流式 `finish_reason: "tool_calls"`，流式构造 `tool_call` chunks）。

但需要区分两个层面：

| 层面 | 是否支持 | 说明 |
|------|----------|------|
| **能力**（能否产出 tool_calls） | ✅ 支持 | 实测 42/46 轮产出可解析的 tool_calls（91.3%） |
| **可靠性**（长链路稳定命中） | ⚠️ 有限 | 精确命中率 76.1%，存在选错工具、忽略工具、超时、上游限流 |

因此，README 早期「不支持 Function Calling / Tool Use」的表述**与现状不符**；但「不适合生产级编程智能体」的提醒在**可靠性**层面仍有依据。

## 二、实现机制（源码依据）

接口层的工具调用是**提示词模拟**，而非底层网页端点的原生结构化输出：

1. 请求体定义 `tools` 与 `tool_choice`（`unified_server.py` 请求模型）。
2. 带 `tools` 时，请求路由到 expert 模型，`convert_messages_with_tools()` 将工具定义注入提示词。
3. 模型以 `<tool_call>{"name":...,"arguments":{...}}</tool_call>` 标签输出。
4. `parse_tool_calls_xml()` 将其还原为 OpenAI 格式 `tool_calls`；流式场景下构造 `tool_call` chunks。

该设计由 `tool_calling.py` 实现，符合其文件头自述：「Converts OpenAI-format tools into prompt injection, parses tool_call tags from model output, converts back to OpenAI-format response」。

## 三、测试方法

- **测试脚本**：仓库自带 `tests/test_tool_reliability.py`（模拟 OpenCode 行为，8 个工具：read_file / edit_file / write_file / grep / glob / bash / todowrite / webfetch）。
- **被测对象**：本地自建 doubao2api 实例（已登录豆包会话）。
- **请求形式**：`model=doubao` + `tools`，OpenAI Chat Completions 兼容。
- **轮次与间隔**：目标 50 轮，每轮间隔 8 秒以规避限流。
- **鉴权**：被测实例开启 API Key 鉴权，测试客户端在请求头携带 Bearer token（token 不在此报告中记录）。

> 说明：`test_tool_reliability.py` 自带的请求未携带鉴权头。仓库现已提供 `tests/run_tool_reliability_auth.py`，在不修改原脚本的前提下为请求注入 `Authorization: Bearer <key>`；具体复现命令见第七节。

## 四、测试结果

### 4.1 总体指标

| 指标 | 数值 |
|------|------|
| 计划轮次 | 50 |
| 实际完成轮次 | 46（第 46 轮触发上游限流 502，测试自带的「服务不可用」保护主动停止） |
| 完全正确（correct_tool） | 35（76.1%） |
| 部分正确（wrong_tool，返回了合法但非预期的工具） | 7（15.2%） |
| 硬失败 | 4（8.7%） |
| **产出可用 tool_calls 的比例** | **42/46 = 91.3%** |

### 4.2 失败模式分布

| 失败模式 | 次数 | 含义 |
|----------|------|------|
| `wrong_tool` | 7 | 返回了合法但非预期的工具（如该 edit_file 却 read_file） |
| `timeout` | 2 | 请求超过 120 秒超时 |
| `no_tool_call` | 1 | 模型忽略工具，直接用自然语言作答 |
| `http_502` | 1 | 上游限流（错误码 710022004），导致测试提前终止 |

本次**未出现** `malformed_xml`（XML 结构损坏）与 `search_hijack`（搜索劫持）两类最严重的解析故障。

### 4.3 延迟统计（秒）

| Min | Max | Avg | P95 |
|-----|-----|-----|-----|
| 0.4 | 120.1 | 16.7 | 68.9 |

> 说明：Min 为限流失败轮（立即返回）；Max 为超时轮（120s 上限）；P95 偏高主要受 expert 模型 + 长上下文影响。

### 4.4 上下文增长

- 起始：3,468 字符
- 结束：45,293 字符
- 增长：约 41,825 字符 / 46 轮

提示词注入方案会累积完整对话历史，上下文随轮次持续增长，这也是后段延迟升高与选错概率上升的相关因素。

### 4.5 逐轮明细

| 轮 | 阶段 | 期望工具 | 结果 | 延迟(s) |
|----|------|----------|------|---------|
| 01 | EXPLORE | glob | OK | 5.8 |
| 02 | EXPLORE | read_file | OK | 5.4 |
| 03 | EXPLORE | read_file | OK | 4.8 |
| 04 | EXPLORE | grep | OK | 3.6 |
| 05 | EXPLORE | read_file | OK | 5.4 |
| 06 | EXPLORE | bash | OK | 5.9 |
| 07 | EXPLORE | grep | OK | 4.8 |
| 08 | EXPLORE | read_file | OK | 6.0 |
| 09 | EXPLORE | read_file | OK | 5.3 |
| 10 | EXPLORE | read_file | OK | 3.8 |
| 11 | PLAN | todowrite | PARTIAL（选错：edit_file） | 6.6 |
| 12 | PLAN | text | OK（自然语言） | 16.6 |
| 13 | PLAN | grep | TIMEOUT | 120.1 |
| 14 | PLAN | read_file | OK | 6.5 |
| 15 | PLAN | todowrite | OK | 46.2 |
| 16 | IMPL | edit_file | OK | 16.0 |
| 17 | IMPL | edit_file | PARTIAL（选错：read_file） | 7.1 |
| 18 | IMPL | edit_file | PARTIAL（选错：read_file） | 4.9 |
| 19 | IMPL | edit_file | PARTIAL（选错：read_file） | 5.8 |
| 20 | IMPL | edit_file | OK | 19.5 |
| 21 | IMPL | write_file | OK | 17.2 |
| 22 | IMPL | bash | OK | 15.8 |
| 23 | IMPL | bash | OK | 12.2 |
| 24 | IMPL | read_file | OK | 3.5 |
| 25 | IMPL | edit_file | PARTIAL（选错：read_file） | 8.1 |
| 26 | IMPL | edit_file | OK | 68.9 |
| 27 | IMPL | edit_file | OK | 41.4 |
| 28 | IMPL | write_file | PARTIAL（选错：edit_file） | 19.8 |
| 29 | IMPL | read_file | OK | 6.0 |
| 30 | IMPL | edit_file | OK | 7.1 |
| 31 | IMPL | bash | OK | 6.0 |
| 32 | IMPL | read_file | OK | 5.6 |
| 33 | IMPL | bash | OK | 6.9 |
| 34 | IMPL | bash | OK | 4.1 |
| 35 | IMPL | bash | OK | 5.0 |
| 36 | TEST | bash | FAIL（no_tool_call） | 16.2 |
| 37 | TEST | edit_file | PARTIAL（选错：bash） | 18.4 |
| 38 | TEST | bash | OK | 10.2 |
| 39 | TEST | bash | OK | 6.4 |
| 40 | TEST | grep | OK | 6.4 |
| 41 | TEST | edit_file | OK | 12.4 |
| 42 | TEST | edit_file | TIMEOUT | 120.1 |
| 43 | TEST | bash | OK | 7.3 |
| 44 | TEST | grep | OK | 34.7 |
| 45 | TEST | bash | OK | 5.9 |
| 46 | CLEANUP | todowrite | FAIL（http_502 限流） | 0.4 |

## 五、解读

1. **能力成立**：91.3% 的轮次产出了可解析的标准 `tool_calls`，证明接口层的工具调用链路端到端可用，README 早期「不支持」的判断应更新。
2. **可靠性有边界**：仅 76.1% 精确命中；`wrong_tool`（选错合法工具）是主要偏差（7 次），对 coding agent 可能造成读改错位；`no_tool_call` 与 `timeout` 会造成单步中断。
3. **限流是硬约束**：8 秒间隔下仍在第 46 轮触发上游限流（502 / 710022004），说明持续高频调用需要更保守的退避与并发控制。
4. **延迟偏高**：平均 16.7s、P95 68.9s，长上下文阶段明显变慢，适合交互式/低并发场景，不适合对时延敏感的生产链路。

## 六、建议

- **接入 Agent 后端可用，但需兜底**：对 `wrong_tool` / `no_tool_call` 做重试与结果校验，避免单步失败拖垮长任务。
- **务必做限流与退避**：生产环境应加入指数退避、并发上限与失败熔断。
- **生产级可靠性优先走官方原生 API**：本项目的工具调用为提示词模拟，延迟与稳定性不足以支撑高可靠长链路编程智能体；对可靠性敏感的场景建议使用原生支持 Function Calling 的官方 API。

## 七、复现方式

仓库在 `tests/` 下提供了带鉴权的运行器 `run_tool_reliability_auth.py`：它复用 `test_tool_reliability.py` 的场景、工具与判定，仅额外负责注入鉴权头与传递参数，**不修改**原测试脚本。

```bash
# 1) 启动并登录 doubao2api 实例（见主 README「快速开始」）

# 2) 实例未开启鉴权：直接运行
python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090 --rounds 50 --delay 8

# 3) 实例开启 API Key 鉴权：用环境变量传 key（避免写入 shell 历史）
export DOUBAO_API_KEY=your-key
python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090
# 或显式传参：--api-key your-key

# 4) 自定义报告输出位置
python tests/run_tool_reliability_auth.py --base-url http://127.0.0.1:9090 --out ./report.json
```

参数说明：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--base-url` | `http://127.0.0.1:9090` | 被测实例地址 |
| `--rounds` | 50 | 测试轮次 |
| `--delay` | 8 | 每轮间隔秒数（防限流） |
| `--api-key` | 读 `DOUBAO_API_KEY` | Bearer token；为空则不加鉴权头 |
| `--out` | 系统临时目录 | 报告输出路径 |

被测实例需已完成登录。若服务中途不可用或触发限流，脚本会打印 `SERVICE DOWN - STOPPING TEST` 并提前结束（本报告即因此停在第 46 轮）。

---

*本报告数据来自 2026-11-13 的一次真实运行，仅反映当次实例与网络条件下的表现；不同环境、账号与会话状态下结果可能波动。*
