<script setup>
import { computed } from 'vue' // 根据检索响应派生统计卡和警告列表。

const props = defineProps({ // 声明父页面传入的多源检索响应。
  result: { type: Object, required: true }, // 要求提供完整响应对象。
})

const isMultiRound = computed(() => Boolean(props.result.run_state)) // 区分新多轮响应与兼容保留的旧单轮响应。
const runState = computed(() => props.result.run_state || {}) // 提取多轮运行状态并在旧响应时提供稳定空对象。
const coverageReport = computed(() => props.result.coverage_report || runState.value.coverage_report || null) // 读取累计候选的最终覆盖报告。
const fusedCount = computed(() => Math.max(0, (props.result.raw_paper_count ?? 0) - (props.result.merged_paper_count ?? 0))) // 根据旧响应原始与合并数量推导融合后候选数。
const filteredCount = computed(() => Math.max(0, fusedCount.value - (props.result.filtered_paper_count ?? 0))) // 推导旧响应规则过滤后的候选数。
const semanticCount = computed(() => Math.max(0, filteredCount.value - (props.result.semantic_truncated_count ?? 0))) // 推导旧响应 BGE-M3 截断后的候选数。
const crossEncoderCount = computed(() => Math.max(0, semanticCount.value - (props.result.cross_encoder_truncated_count ?? 0))) // 推导旧响应 Cross Encoder 截断后的候选数。

const singleRoundStageStats = computed(() => [ // 按旧单轮检索流程组织阶段数量。
  { label: '多源召回', value: props.result.raw_paper_count ?? 0, detail: sourceSummary.value }, // 展示来源级原始论文数。
  { label: '身份融合', value: fusedCount.value, detail: `合并 ${props.result.merged_paper_count ?? 0} 条重复记录` }, // 展示融合后规模。
  { label: '规则过滤', value: filteredCount.value, detail: `移除 ${props.result.filtered_paper_count ?? 0} 篇` }, // 展示硬规则过滤结果。
  { label: '语义排序', value: semanticCount.value, detail: `BGE 截断 ${props.result.semantic_truncated_count ?? 0} 篇` }, // 展示 BGE 阶段截断统计。
  { label: '精细重排', value: crossEncoderCount.value, detail: `Cross Encoder 截断 ${props.result.cross_encoder_truncated_count ?? 0} 篇` }, // 展示 Cross Encoder 阶段统计。
  { label: '最终推荐', value: props.result.papers?.length ?? 0, detail: `LLM 核验淘汰 ${props.result.llm_rejected_count ?? 0} 篇` }, // 展示最终证据化结果数量。
])
const multiRoundStageStats = computed(() => [ // 将多轮控制器状态组织为可扫读的过程摘要。
  { label: '搜索轮次', value: runState.value.current_round ?? 0, detail: `最多 ${runState.value.max_rounds ?? 0} 轮` }, // 展示实际完成轮次和硬上限。
  { label: '来源调用', value: runState.value.api_call_count ?? 0, detail: sourceSummary.value }, // 展示所有轮次累计的来源调用和结果统计。
  { label: '累计候选', value: runState.value.candidate_ids?.length ?? props.result.papers?.length ?? 0, detail: `最终保留 ${props.result.papers?.length ?? 0} 篇` }, // 区分跨轮候选与最终显示论文。
  { label: '高相关', value: coverageReport.value?.high_relevance_count ?? 0, detail: `目标 ${coverageReport.value?.target_count ?? 0} 篇` }, // 展示覆盖报告的目标完成度。
  { label: '部分相关', value: coverageReport.value?.partial_relevance_count ?? 0, detail: `本轮新增 ${coverageReport.value?.new_valid_count ?? 0} 篇` }, // 展示仍需人工核验的候选与边际收益。
  { label: '最终推荐', value: props.result.papers?.length ?? 0, detail: runState.value.stop_reason || '已完成' }, // 展示控制器停止原因而非将不足结果误解为失败。
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
  <!-- 用流程卡展示候选如何逐层收敛，避免把最终数量误解为召回不足。 -->
  <section class="stats-panel" aria-labelledby="stats-heading">
    <div class="section-heading">
      <div>
        <p class="kicker">SEARCH TRACE</p>
        <h2 id="stats-heading">检索过程</h2>
      </div>
      <p v-if="result.llm_model_name || result.query_planning_model_name" class="model-meta">{{ result.llm_model_name || result.query_planning_model_name }} · {{ (result.llm_prompt_tokens || result.query_planning_prompt_tokens || 0) + (result.llm_completion_tokens || result.query_planning_completion_tokens || 0) }} tokens</p>
    </div>
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
.stats-panel { /* 包裹完整检索轨迹。 */
  padding: 1.5rem; /* 为统计内容提供稳定留白。 */
  border: 1px solid #dfe7ef; /* 使用浅边框区分背景。 */
  border-radius: 1.25rem; /* 与搜索面板保持一致圆角。 */
  background: rgba(255, 255, 255, 0.88); /* 提供轻盈白色信息面板。 */
  box-shadow: 0 16px 40px rgba(30, 64, 92, 0.06); /* 与页面背景建立层次。 */
}

.section-heading { /* 横向排列标题和模型统计。 */
  display: flex; /* 使用弹性布局。 */
  align-items: end; /* 将模型信息与标题基线对齐。 */
  justify-content: space-between; /* 分置标题和模型信息。 */
  gap: 1rem; /* 避免窄屏内容相贴。 */
  margin-bottom: 1.1rem; /* 与流程卡分隔。 */
}

.kicker { /* 显示英文辅助标题。 */
  margin: 0 0 0.25rem; /* 与中文标题形成紧凑组合。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-size: 0.68rem; /* 降低英文辅助信息权重。 */
  font-weight: 800; /* 保持小字号清晰。 */
  letter-spacing: 0.16em; /* 建立标签感。 */
}

h2 { /* 设置区块标题。 */
  margin: 0; /* 移除默认标题间距。 */
  color: #18354f; /* 使用深蓝主文字。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 延续学术出版气质。 */
  font-size: 1.35rem; /* 清晰但不压过页面主标题。 */
}

.model-meta { /* 展示模型和 Token 成本。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #718096; /* 作为辅助元数据。 */
  font-size: 0.75rem; /* 控制信息密度。 */
}

.stage-grid { /* 横向展示六个处理阶段。 */
  display: grid; /* 使用等宽网格。 */
  grid-template-columns: repeat(6, minmax(0, 1fr)); /* 保持阶段宽度一致。 */
  gap: 0.65rem; /* 分隔相邻阶段。 */
  margin: 0; /* 移除有序列表默认外边距。 */
  padding: 0; /* 移除列表默认缩进。 */
  list-style: none; /* 使用自定义阶段编号。 */
}

.stage-card { /* 展示单个阶段数量和说明。 */
  position: relative; /* 为连接线和编号提供定位上下文。 */
  display: grid; /* 纵向组织阶段内容。 */
  min-height: 8.3rem; /* 保持所有卡片等高。 */
  align-content: start; /* 从顶部排列内容。 */
  padding: 0.9rem; /* 提供卡片内部留白。 */
  border-radius: 0.9rem; /* 使用柔和圆角。 */
  background: #f6f9fc; /* 以浅蓝灰区分阶段。 */
}

.stage-index { /* 显示阶段序号。 */
  color: #8ba1b5; /* 弱化编号权重。 */
  font-family: Georgia, serif; /* 使用衬线数字增加流程感。 */
  font-size: 0.7rem; /* 保持编号辅助地位。 */
}

.stage-card strong { /* 突出阶段论文数量。 */
  margin-top: 0.45rem; /* 与编号分隔。 */
  color: #173f7a; /* 使用品牌主色。 */
  font-family: Georgia, serif; /* 强化数字辨识度。 */
  font-size: 1.75rem; /* 让数量成为卡片视觉焦点。 */
  line-height: 1; /* 紧凑数字行高。 */
}

.stage-card > span:not(.stage-index) { /* 显示阶段名称。 */
  margin-top: 0.55rem; /* 与数字分隔。 */
  color: #334e68; /* 使用稳定正文色。 */
  font-size: 0.78rem; /* 保持流程卡紧凑。 */
  font-weight: 700; /* 强调阶段语义。 */
}

.stage-card small { /* 显示阶段补充统计。 */
  margin-top: 0.3rem; /* 与阶段名称保持关联。 */
  overflow: hidden; /* 防止长来源统计撑开布局。 */
  color: #8293a5; /* 降低细节权重。 */
  font-size: 0.66rem; /* 控制六列信息密度。 */
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

@media (max-width: 980px) { /* 在中等屏幕将六列折为三列。 */
  .stage-grid { /* 调整统计网格。 */
    grid-template-columns: repeat(3, minmax(0, 1fr)); /* 每行展示三个阶段。 */
  }
}

@media (max-width: 560px) { /* 在手机上进一步收敛布局。 */
  .stage-grid { /* 调整手机统计网格。 */
    grid-template-columns: repeat(2, minmax(0, 1fr)); /* 每行展示两个阶段。 */
  }

  .section-heading { /* 允许模型信息换行。 */
    align-items: start; /* 使用左对齐提升窄屏可读性。 */
    flex-direction: column; /* 将模型信息放在标题下。 */
  }
}
</style>
