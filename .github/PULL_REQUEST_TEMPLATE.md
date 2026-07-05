<!--
For Chinese contributors: 请直接用中文填写。
For English contributors: please fill in English. All fields marked (EN) accept English.
-->

## PR Type

- [ ] fix
- [ ] feat
- [ ] refactor
- [ ] docs
- [ ] chore
- [ ] test

## Background And Problem

请描述当前问题、影响范围与触发场景。
*(EN) Describe the problem, its impact, and what triggers it.*

## Scope Of Change

请按当前 HEAD 的完整 `git diff` 全量列出变更范围（不得遗漏文件名）：

- 当前 Head（必填）：
  - `git rev-parse HEAD`
- 当前 Base（建议）：
  - `git merge-base origin/main HEAD`
- 文件总数（必填）：
  - `git diff --name-only --diff-filter=ACMRDTUXB $(git merge-base origin/main HEAD) HEAD | wc -l`
- 行变更（增/删，必填）：
  - `git diff --stat --numstat $(git merge-base origin/main HEAD) HEAD`
- 文件清单（必填）：
  - `git diff --name-only --diff-filter=ACMRDTUXB $(git merge-base origin/main HEAD) HEAD`

请直接粘贴上述命令输出，不得截断。缺少以下类别文件将视为 Scope 未闭环：

- `api/v1/endpoints/**`
- `api/v1/schemas/**`
- `src/services/decision_signal_service.py`
- `apps/dsa-web/src/components/report/**`
- `apps/dsa-web/src/utils/**`
- `apps/dsa-web/src/types/analysis.ts`
- `tests/**`
- 及本次涉及的治理/文档文件（`docs/**`、`.github/**`、`AGENTS.md` 等）

请在正文中同步写出本 PR 的当前 Head（如 `Current HEAD: <hash>`）。

若本 PR 包含 `.github/**`、`AGENTS.md`、`CLAUDE.md`、`.github/instructions/**`、`.claude/skills/**` 等协作与治理文件，请补充“变更原因 + 影响面 + 回滚方式（默认 revert）”。
并补充该类文件的治理资产验证命令与结果（至少包含 `python scripts/check_ai_assets.py` 的结果）。

## EXTRACT_PROMPT Change

若本 PR 修改了 `src/services/image_stock_extractor.py` 的 `EXTRACT_PROMPT`，请在下方贴出本次 PR 最新完整 prompt。

- 变更前：
- 变更后：

## Issue Link

必须填写以下之一：
- `Fixes #<issue_number>`
- `Refs #<issue_number>`
- 无 Issue 时说明原因与验收标准。

## Visual Evidence (if applicable)

若修改了报告格式、报告渲染效果或 Web UI，请在此处附受影响报告页/页面截图；前后对比优先。

- 必须覆盖：`市场结构上下文/市场位置卡片` 所在的报告详情页（桌面 + 至少一张移动视图）。
- 受影响页面截图示例（至少 2 张）：
  - `report-summary-desktop.png`
  - `report-summary-mobile.png`
- 复现截图命令（至少给出一种）：
  - `cd apps/dsa-web && npx playwright test e2e/report-markdown.spec.ts --grep "copy markdown source code|copy plain text|mobile responsive layout"`
  - 或在有后端/登录凭证情况下运行 `npm run test:smoke` 并说明截图来源。

- 截图链接（或外部可访问证据）：
- 产物路径（示例）：`apps/dsa-web/test-results/**/*.png`
- 复现命令（示例）：
  - `cd apps/dsa-web && npx playwright test e2e/smoke.spec.ts --grep "settings page"`
- 无法截图时需写明原因与可复现替代证据。
  - 本轮需覆盖页面：受影响报告页（例如 `report detail / history / 市场结构卡片`）、首页或设置页关键状态视图（若有）；
  - 不可用真实历史报告时，请补充可复用的 mock 页（含 mock 数据）或 Actions artifact 链接，并在 PR 评论中给出复现步骤与截图入口。

## Verification Commands And Results

请填写你实际执行过的命令和关键结果（不要只写“已测试”）。

- ai-governance：pass / fail（链接）
- backend-gate：pass / fail（链接）
- docker-build：pass / fail（链接）
- web-gate：pass / fail（链接）
- check-ai-assets：pass / fail（命令：`python scripts/check_ai_assets.py`，建议附输出/日志）

- Head/CI 结果（建议粘贴一行）：`当前 Head CI：ai-governance:..., backend-gate:..., docker-build:..., web-gate:...`

## Compatibility And Risk

请说明兼容性影响、潜在风险（如无请写 `None`）。

- 是否触及运行时配置、路由或迁移语义：是 / 否
- 是否触及 provider/model/base URL 相关语义：是 / 否
  - 若是，必须逐条说明：
    - 命中来源（文件与关键词/扫描项）
    - 是否为 false positive（是 / 否）
    - 旧行为是否有静默清理或迁移语义影响（有 / 无）
    - 回滚方案与验证依据
  - 请确认命中源是否属于以下非运行时改动：仅模板文本、注释、测试、文档、单测 mock；若是 false positive 请在下方逐条列明“文件-命中文本-判定依据”。
  - 若存在运行时影响或兼容风险，请同步列出“旧配置迁移路径（含回退步骤）”与验证依据（例如回归测试、CI 结果或本地复现命令）。
  - 若仅为展示字段（如历史快照元数据）改动，请在此明确写明：历史展示字段不影响运行时调用路径、持久化清理与迁移。

## Rollback Plan

请写一条可执行的回滚方案（必填）。

- 默认可回退方案：`revert this PR`，并说明是否需要外部配置回填。

## Checklist

- [ ] 本 PR 有明确动机和业务价值 / This PR has a clear motivation and value
- [ ] 已提供可复现的验证命令与结果 / Reproducible verification commands and results are included
- [ ] 已评估兼容性与风险 / Compatibility and risk have been assessed
- [ ] 若触及 provider/model/base URL 或运行时配置迁移扫描项，已逐条说明命中来源、是否 false positive、静默清理/回退路径与验证依据
- [ ] 已提供回滚方案 / A rollback plan is provided
- [ ] 若修改报告格式或 Web UI 界面，已在 PR 正文/评论附受影响页面截图，不提交一次性截图文件到仓库 / If report formatting or Web UI changed, attach screenshots in PR body/comments and do not commit one-off screenshots in the repo
- [ ] 若涉及用户可见变更，已同步更新相关文档与 `docs/CHANGELOG.md`；`README.md` 仅用于首页级信息变化，细节优先写入 `docs/*.md` / If user-visible changes are included, relevant docs and `docs/CHANGELOG.md` are updated
