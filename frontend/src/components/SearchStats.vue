<script setup>
import { computed } from 'vue' // 根据检索响应派生统计卡和警告列表。

const props = defineProps({ // 声明父页面传入的多源检索响应。
  result: { type: Object, required: true }, // 要求提供完整响应对象。
})

const isMultiRound = computed(() => Boolean(props.result.run_state)) // 区分新多轮响应与兼容保留的旧单轮响应。
const runState = computed(() => props.result.run_state || {}) // 提取多轮运行状态并在旧响应时提供稳定空对象。
const coverageReport = computed(() => props.result.coverage_report || runState.value.coverage_report || null) // 读取累计候选的最终覆盖报告。
const fusedCount = computed(() => Math.max(0, (props.result.raw_paper_count ?? 0) - (props.result.merged_paper_count ?? 0))) // 根据旧响应原始与合并数量推导融合后候选数。

const singleRoundStageStats = computed(() => [ // 旧单轮响应仅保留三个不与概览重复的过程节点。
  { label: '多源召回', value: props.result.raw_paper_count ?? 0, detail: sourceSummary.value }, // 展示来源级原始论文数。
  { label: '身份融合', value: fusedCount.value, detail: `合并 ${props.result.merged_paper_count ?? 0} 条重复记录` }, // 展示融合后规模。
  { label: '规则筛选', value: Math.max(0, fusedCount.value - (props.result.filtered_paper_count ?? 0)), detail: `规则移除 ${props.result.filtered_paper_count ?? 0} 篇` }, // 展示进入排序前的可用候选规模。
])
const multiRoundStageStats = computed(() => [ // 多轮响应只保留轮次、来源和候选演进。
  { label: '搜索轮次', value: runState.value.current_round ?? 0, detail: `最多 ${runState.value.max_rounds ?? 0} 轮` }, // 展示实际完成轮次和硬上限。
  { label: '来源调用', value: runState.value.api_call_count ?? 0, detail: sourceSummary.value }, // 展示所有轮次累计的来源调用和结果统计。
  { label: '累计候选', value: runState.value.candidate_ids?.length ?? props.result.papers?.length ?? 0, detail: `本轮新增 ${coverageReport.value?.new_valid_count ?? 0} 篇` }, // 区分跨轮候选与本轮边际收益。
])
const stageStats = computed(() => isMultiRound.value ? multiRoundStageStats.value : singleRoundStageStats.value) // 按响应类型选择正确的过程统计。

const sourceSummary = computed(() => { // 将来源数量压缩为可扫读文本。
  const entries = Object.entries(props.result.source_counts || {}) // 获取实际执行来源和数量。
  return entries.length ? entries.map(([source, count]) => `${source} ${count}`).join(' · ') : '暂无来源统计' // 在无统计时提供稳定占位。
})

const warnings = computed(() => { // 汇总来源和排序阶段的安全降级摘要。
  const sourceWarnings = Object.entries(props.result.source_errors || {}).map(([source, message]) => `${source}：${message}`) // 格式化来源错误。
  const rankingWarnings = [props.result.semantic_ranking_error, props.result.cross_encoder_ranking_error, props.result.llm_ranking_error].filter(Boolean) // 收集各排序层降级信息。
  const stateWarnings = Array.isArray(runState.value.warnings) ? runState.value.warnings : [] // 补充多轮运行中安全可展示的控制器提示。
  return [...sourceWarnings, ...rankingWarnings, ...stateWarnings] // 保持来源错误在前、排序降级和控制器提示在后。
})
</script>

<template>
  <!-- 仅在概览的折叠诊断区展示候选演进，避免重复陈列最终结论。 -->
  <section class="stats-panel" aria-label="检索过程诊断">
    <p v-if="result.llm_model_name || result.query_planning_model_name" class="model-meta">{{ result.llm_model_name || result.query_planning_model_name }} · {{ (result.llm_prompt_tokens || result.query_planning_prompt_tokens || 0) + (result.llm_completion_tokens || result.query_planning_completion_tokens || 0) }} tokens</p>
    <ol class="stage-grid">
      <li v-for="(stage, index) in stageStats" :key="stage.label" class="stage-card">
        <span class="stage-index">{{ String(index + 1).padStart(2, '0') }}</span>
        <strong>{{ stage.value }}</strong>
        <span>{{ stage.label }}</span>
        <small>{{ stage.detail }}</small>
      </li>
    </ol>
    <div v-if="warnings.length" class="warning-strip" role="status">
      <strong>本次检索已降级</strong>
      <span v-for="warning in warnings" :key="warning">{{ warning }}</span>
    </div>
  </section>
</template>

<style scoped>
.stats-panel { /* 作为概览折叠区内部内容，不再形成独立大面板。 */
  display: grid; /* 纵向组织模型元数据、过程统计和降级提示。 */
  gap: 0.75rem; /* 保持折叠内容的阅读层级。 */
}

.model-meta { /* 展示模型和 Token 成本。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #718096; /* 作为辅助元数据。 */
  font-size: 0.75rem; /* 控制信息密度。 */
}

.stage-grid { /* 横向展示三个不重复的紧凑过程节点。 */
  display: grid; /* 使用网格稳定排列过程统计。 */
  grid-template-columns: repeat(3, minmax(0, 12rem)); /* 限制卡片宽度，避免少量统计被放大铺满整行。 */
  justify-content: start; /* 让统计从左侧紧凑排列。 */
  gap: 0.5rem; /* 缩小相邻过程节点间距。 */
  margin: 0; /* 移除有序列表默认外边距。 */
  padding: 0; /* 移除列表默认缩进。 */
  list-style: none; /* 使用自定义阶段编号。 */
}

.stage-card { /* 展示单个阶段数量和说明。 */
  display: grid; /* 纵向组织阶段内容。 */
  min-height: 5rem; /* 将诊断卡控制为辅助信息高度。 */
  align-content: start; /* 从顶部排列内容。 */
  padding: 0.65rem 0.75rem; /* 缩小卡片留白以降低过程区视觉重量。 */
  border-radius: 0.65rem; /* 使用较紧凑的圆角。 */
  background: #f6f9fc; /* 以浅蓝灰区分阶段。 */
}

.stage-index { /* 显示阶段序号。 */
  color: #8ba1b5; /* 弱化编号权重。 */
  font-family: Georgia, serif; /* 使用衬线数字增加流程感。 */
  font-size: 0.62rem; /* 保持编号辅助地位。 */
}

.stage-card strong { /* 突出阶段论文数量。 */
  margin-top: 0.2rem; /* 与编号保持紧凑分隔。 */
  color: #173f7a; /* 使用品牌主色。 */
  font-family: Georgia, serif; /* 强化数字辨识度。 */
  font-size: 1.2rem; /* 将过程数字降为辅助统计而非页面主焦点。 */
  line-height: 1; /* 紧凑数字行高。 */
}

.stage-card > span:not(.stage-index) { /* 显示阶段名称。 */
  margin-top: 0.28rem; /* 与数字保持紧凑分隔。 */
  color: #334e68; /* 使用稳定正文色。 */
  font-size: 0.68rem; /* 保持流程卡紧凑。 */
  font-weight: 700; /* 强调阶段语义。 */
}

.stage-card small { /* 显示阶段补充统计。 */
  margin-top: 0.18rem; /* 与阶段名称保持关联。 */
  overflow: hidden; /* 防止长来源统计撑开布局。 */
  color: #8293a5; /* 降低细节权重。 */
  font-size: 0.6rem; /* 控制过程说明的信息密度。 */
  line-height: 1.45; /* 保证多行细节可读。 */
  text-overflow: ellipsis; /* 超长内容使用省略提示。 */
}

.warning-strip { /* 展示不阻断结果的降级信息。 */
  display: flex; /* 横向排列标题和多个警告。 */
  flex-wrap: wrap; /* 窄屏允许警告换行。 */
  gap: 0.45rem 0.85rem; /* 区分不同警告。 */
  margin-top: 0.85rem; /* 与流程卡分隔。 */
  padding: 0.75rem 0.9rem; /* 提供警告背景留白。 */
  border-radius: 0.75rem; /* 使用圆角提示条。 */
  color: #8a5a18; /* 使用克制的琥珀文字。 */
  background: #fff8e8; /* 使用浅琥珀底色。 */
  font-size: 0.72rem; /* 保持警告为辅助信息。 */
}

@media (max-width: 980px) { /* 在中等屏幕保持三个过程节点并收紧宽度。 */
  .stage-grid { /* 调整统计网格。 */
    grid-template-columns: repeat(3, minmax(0, 1fr)); /* 每行仍展示三个阶段。 */
  }
}

@media (max-width: 560px) { /* 在手机上进一步收敛布局。 */
  .stage-grid { /* 调整手机统计网格。 */
    grid-template-columns: 1fr; /* 单列显示以避免过程说明被过度截断。 */
  }
}
</style>
