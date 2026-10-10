# MVP-BACKLOG — vocab-app

> 版本 0.3 | 2026-10-05

## 已交付（v0.1 骨架）

- [x] 项目工作空间 `D:\Projects\vocab-app`，与 Hermes 桌面 Project `vocab-app` 绑定
- [x] `AGENTS.md` 项目宪法（数据契约 + 记忆曲线规则 + 授权边界 + Karpathy 12 全文）
- [x] `交付成果\accounts\<账户>\` 账户隔离结构
- [x] `src\prompts\ljg-word.md` 解词规范（项目内资产，不注册 Hermes 技能）
- [x] `src\review.py` 复习引擎：`stat` / `due` / `exam` / `grade`
- [x] 端到端验证：exam → grade → frontmatter 回写（正文不受影响）✅
- [x] 第一个真实词条 `Serendipity.md`

## v0.1.1 — DeepSeek 查词历史导入（2026-09-11）

- [x] 从 `https://chat.deepseek.com/share/64lgt4sru5lpavgwvy` 抓取完整对话（52 组问答，60,196 字符）
- [x] 导入 **48 个词条**到 `交付成果\accounts\jason\词库\`
- [x] 原始对话存档 → `交付成果\accounts\jason\历史\2026-09-11_DeepSeek_64lgt4sru5lpavgwvy.md`
- [x] 词库规模：49 词，48 词今日到期
- [x] 拼写纠错由 DeepSeek 自身完成，原始输入记入 `queried_as`：
      `emphathize→Empathize`、`povake→Provoke`、`maturinty→Maturity`、`fiere→Fierce`、
      `Paradime→Paradigm`、`Devision→Division`
- [x] 同词重复查询合并：`Constraints` ×2、`Dividend` ×2（第二份存为"备选解析"）
- [x] `universal vs general` 对比挂在 `Universal.md` 的"易混对比"节

导入工具：`D:\hermes\cache\browser-use\workspace\20260911_125635_e306dc\import_deepseek.py`（幂等，重复运行会跳过已存在词条）

## v0.2 候选（按价值排序）

| # | 项 | 价值 | 成本 | 备注 |
|---|----|------|------|------|
| 1 | **错词词卡**：考试错词自动跑 ljg-word-flow 铸 PNG 词卡 | 高 | 低 | 依赖已就绪（ljg-card + Playwright chromium 都在） |
| 2 | **查词入口**：`python src/lookup.py <word>` 生成词条骨架 | 中 | 低 | 解词内容由 agent 填，脚本只负责建文件 + 查重 |
| 3 | **周报**：每周日生成"本周新增 / 到期积压 / 正确率趋势" | 中 | 低 | 复用到 cron + 钉钉推送链路 |
| 4 | **词根聚类复习**：按 `roots` / `themes` 成组出题 | 中 | 中 | 需要 ≥50 词才有意义 |
| 5 | **定时推送**：cron 每天挑到期词推到钉钉 | 中 | 中 | 用户要求 v1 先手动触发 |
| 6 | **难度自适应**：错题在本次考试内重复出现 | 低 | 中 | |
| 7 | **前端**：本地网页 / 手机壳 | 待定 | 高 | **链路跑顺后再决策** |

## v0.3 — 外部 AI 会话自动导入（2026-10-05）

- [x] `src/import_words.py`：扫描 `deepseek-word*.md`（SyncNos 同步的 DeepSeek 对话全文），只解析 assistant 块，把新词按现有 49 词的规范结构落库；已在库即跳过（一个字节不动），出生初始化调度字段后绝不再碰（ADR-0011）
- [x] cron 薄包装 `D:\hermes\data\scripts\vocab-import.py`（每 30 分钟、`--no-agent`、零 token，由父代理建 cron）
- [x] 确定性自测 25 项全 PASS；真实源 `--dry-run` 精确报 5 词且幂等；`_selftest` 端到端后逐字节恢复

## 明确不做

- 独立 App / 账号系统 / 云端同步 / 发音 / 社交
- 第三方 Python 依赖（见 AGENTS.md §6 依赖锁定）

## 决策记录

| 日期 | 决策 | 理由 |
|------|------|------|
| 2026-09-11 | ljg-word 作为项目内提示词资产，**不注册 Hermes 技能** | 零常驻 token 成本；共享技能库会被外部工具写字段污染 |
| 2026-09-11 | 词库落在 `交付成果\` 而非 Obsidian vault | 用户要求交付物独立、可整体搬走（分析改走脚本，不依赖 Dataview） |
| 2026-09-11 | 记忆曲线用固定阶梯（1/2/4/7/15/30/60/120 天），不用完整 SM-2 | 可解释、可审计、零依赖；效果足够 |
| 2026-09-11 | 题型难度随 stage 上升 | 把"记忆曲线"落到题目上：越熟越难的线索 = retrieval practice |
| 2026-09-11 | 账户用目录隔离，不做鉴权 | MVP 阶段隔离靠约定，不靠权限 |
| 2026-09-21 | 交卷规则改"答了就判"，不再整份跳过 | 部分作答的卷子被冻结、词回池不前进、天天重出同批（ADR-0006） |
| 2026-09-21 | 出卷门控：有未作答旧卷就不出新卷；作废改为显式 `void` 命令 | 用户明确不想反复填同一张卷；系统不再无声作废替用户做决定（ADR-0007） |
| 2026-09-22 | 成绩单词列与错词行加 `[[Word]]` 双链 | 判错即再编码时机，一键回看记忆锚（ADR-0008） |
| 2026-10-02 | 出卷新词保底配额 3 | 新词被非新词永久饿死，21 词从未出卷（ADR-0009） |
| 2026-10-02 | 出卷取词改「新词填空 + 卷长自适应」，新词名额上限 10（=基准卷长，非保底） | 保底制造永久 +N 槽/天负荷；模拟延迟 59.2→0.32 天、积压 1189→11；cap10 与 cap5 压力档结果相同、规则自带限流（ADR-0010） |
| 2026-10-05 | 外部 AI 会话自动导入：cron 每 30 分钟扫 `deepseek-word*.md`，只增不改，幂等靠词库自身而非状态文件 | 手工搬运易漏；词库自身即「已导入」事实，避免两份真相对漂；保护复习历史；出生写一次调度字段后绝不再碰（ADR-0011） |
