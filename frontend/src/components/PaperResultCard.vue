<script setup>
import { computed } from 'vue' // 派生作者、来源、状态和安全链接展示值。

const props = defineProps({ // 声明论文和列表序号输入。
  paper: { type: Object, required: true }, // 接收后端 PaperRecord。
  rank: { type: Number, required: true }, // 接收从一开始的结果排名。
})

const authors = computed(() => { // 将作者列表压缩为适合卡片的文本。
  const names = (props.paper.authors || []).map((author) => author.name).filter(Boolean) // 提取有效作者名称。
  if (!names.length) return '作者信息暂缺' // 为来源缺失作者提供明确占位。
  return names.length > 4 ? `${names.slice(0, 4).join('、')} 等` : names.join('、') // 长作者列表只展示前四位。
})

const sources = computed(() => { // 优先展示完整多源溯源列表。
  const sourceNames = (props.paper.source_records || []).map((record) => record.source).filter(Boolean) // 提取所有融合来源。
  return [...new Set(sourceNames.length ? sourceNames : [props.paper.source])] // 去重并在无溯源时回退主来源。
})

const safePaperUrl = computed(() => { // 只允许浏览器打开 HTTP 或 HTTPS 学术链接。
  const candidate = props.paper.open_access_url // 优先使用来源给出的合法开放访问链接。
  if (!candidate) return null // 无链接时不渲染可点击标题。
  try { // 防止无效或危险协议进入 href。
    const url = new URL(candidate) // 使用浏览器 URL 解析器校验。
    return ['http:', 'https:'].includes(url.protocol) ? url.href : null // 只接受网页协议。
  } catch { // 无法解析的链接视为不可访问。
    return null // 返回空值让标题退化为纯文本。
  }
})

const statusMeta = computed(() => { // 将后端三态核验映射为中文展示。
  const mapping = { // 定义稳定状态标签和样式名。
    satisfied: { label: '约束已满足', className: 'is-satisfied' }, // 表示存在公开证据支持。
    uncertain: { label: '需要进一步核验', className: 'is-uncertain' }, // 表示证据不足。
    not_satisfied: { label: '约束未满足', className: 'is-rejected' }, // 为未来展示被排除结果保留映射。
  }
  return mapping[props.paper.constraint_status] || { label: '尚未核验', className: 'is-unknown' } // LLM 降级时显示中性状态。
})

const scoreLabel = computed(() => { // 将归一化 LLM 分数转为百分比。
  const score = props.paper.llm_relevance_score ?? props.paper.cross_encoder_score // LLM 缺失时回退 Cross Encoder 分数。
  return typeof score === 'number' ? `${Math.round(score * 100)}%` : '—' // 缺失分数时不虚构数值。
})
</script>

<template>
  <!-- 单篇论文卡片将身份元数据、核验证据和推荐理由保持在同一阅读单元。 -->
  <article class="paper-card">
    <div class="rank-column" aria-label="结果排名">
      <span>{{ String(rank).padStart(2, '0') }}</span>
      <small>相关度</small>
      <strong>{{ scoreLabel }}</strong>
    </div>
    <div class="paper-content">
      <div class="paper-badges">
        <span v-for="source in sources" :key="source" class="source-badge">{{ source }}</span>
        <span :class="['status-badge', statusMeta.className]">{{ statusMeta.label }}</span>
        <span v-if="paper.paper_type" class="type-badge">{{ paper.paper_type }}</span>
      </div>
      <h3>
        <a v-if="safePaperUrl" :href="safePaperUrl" target="_blank" rel="noopener noreferrer">{{ paper.title }}</a>
        <span v-else>{{ paper.title }}</span>
      </h3>
      <p class="bibliography">
        <span>{{ authors }}</span>
        <span>{{ paper.year || '年份暂缺' }}</span>
        <span>{{ paper.venue || 'Venue 暂缺' }}</span>
        <span>被引 {{ paper.citation_count || 0 }}</span>
      </p>
      <section v-if="paper.recommendation_reason" class="recommendation" aria-label="推荐理由">
        <span class="recommendation-label">为什么推荐</span>
        <p>{{ paper.recommendation_reason }}</p>
      </section>
      <ul v-if="paper.constraint_evidence?.length" class="evidence-list" aria-label="约束证据">
        <li v-for="evidence in paper.constraint_evidence" :key="evidence">“{{ evidence }}”</li>
      </ul>
      <details v-if="paper.abstract" class="abstract-details">
        <summary>查看摘要</summary>
        <p>{{ paper.abstract }}</p>
      </details>
      <div class="paper-footer">
        <span v-if="paper.doi">DOI {{ paper.doi }}</span>
        <span v-else-if="paper.arxiv_id">arXiv {{ paper.arxiv_id }}</span>
        <span v-if="paper.work_family_id">版本族 {{ paper.work_family_id }}</span>
      </div>
    </div>
  </article>
</template>

<style scoped>
.paper-card { /* 将排名与论文正文组成清晰横向卡片。 */
  display: grid; /* 使用网格固定排名列。 */
  grid-template-columns: 4.5rem minmax(0, 1fr); /* 保持正文可自适应缩放。 */
  overflow: hidden; /* 防止内部背景越过圆角。 */
  border: 1px solid #dfe7ef; /* 用浅边框界定结果。 */
  border-radius: 1.1rem; /* 使用略小于主面板的圆角。 */
  background: #ffffff; /* 确保长文本阅读对比度。 */
  box-shadow: 0 10px 28px rgba(30, 64, 92, 0.045); /* 提供轻微层次。 */
  transition: border-color 160ms ease, transform 160ms ease, box-shadow 160ms ease; /* 平滑响应悬停反馈。 */
}

.paper-card:hover { /* 强化当前扫读的论文卡片。 */
  border-color: #b8ccdc; /* 使用蓝灰强调边框。 */
  box-shadow: 0 18px 34px rgba(30, 64, 92, 0.09); /* 提升悬停层次。 */
  transform: translateY(-2px); /* 轻微上移而不干扰布局。 */
}

.rank-column { /* 展示排名和相关度。 */
  display: flex; /* 使用纵向弹性布局。 */
  align-items: center; /* 水平居中排名内容。 */
  padding: 1.25rem 0.65rem; /* 保留竖向呼吸。 */
  border-right: 1px solid #e5edf3; /* 分隔排名与正文。 */
  color: #64748b; /* 使用辅助文字色。 */
  background: linear-gradient(180deg, #f4f8fb, #fbfcfd); /* 增加排名列视觉区分。 */
  flex-direction: column; /* 纵向排列编号、标签和分数。 */
}

.rank-column > span { /* 突出列表排名。 */
  color: #9aafc0; /* 保持排名不压过标题。 */
  font-family: Georgia, serif; /* 使用衬线数字。 */
  font-size: 1rem; /* 提供清晰排名定位。 */
}

.rank-column small { /* 标记相关度分数。 */
  margin-top: auto; /* 将分数区域推到底部。 */
  font-size: 0.62rem; /* 保持辅助标签紧凑。 */
}

.rank-column strong { /* 展示百分比相关度。 */
  margin-top: 0.2rem; /* 与标签保持关联。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-family: Georgia, serif; /* 使用衬线数字提升辨识。 */
  font-size: 0.9rem; /* 保持分数次于标题。 */
}

.paper-content { /* 包裹论文主要信息。 */
  min-width: 0; /* 允许长标题在网格中正确换行。 */
  padding: 1.25rem 1.35rem; /* 提供卡片阅读留白。 */
}

.paper-badges { /* 横向排列来源与核验状态。 */
  display: flex; /* 使用弹性布局。 */
  flex-wrap: wrap; /* 来源较多时允许换行。 */
  gap: 0.4rem; /* 分隔胶囊标签。 */
  align-items: center; /* 对齐不同标签。 */
}

.source-badge,
.status-badge,
.type-badge { /* 统一标签基础样式。 */
  padding: 0.22rem 0.48rem; /* 形成紧凑胶囊。 */
  border-radius: 999px; /* 使用完整圆角。 */
  font-size: 0.64rem; /* 控制元数据密度。 */
  font-weight: 700; /* 保证小字号清晰。 */
  letter-spacing: 0.02em; /* 增强英文来源可读性。 */
}

.source-badge { /* 展示论文来源。 */
  color: #24577a; /* 使用蓝色文字。 */
  background: #eaf3f8; /* 使用浅蓝背景。 */
}

.type-badge { /* 展示论文类型。 */
  color: #667085; /* 使用中性文字。 */
  background: #f0f2f5; /* 使用中性背景。 */
}

.status-badge.is-satisfied { /* 标记证据支持的约束满足。 */
  color: #28745a; /* 使用可信绿色。 */
  background: #e8f7f0; /* 使用浅绿背景。 */
}

.status-badge.is-uncertain { /* 标记证据不足。 */
  color: #8a5a18; /* 使用琥珀文字。 */
  background: #fff4d8; /* 使用浅琥珀背景。 */
}

.status-badge.is-rejected,
.status-badge.is-unknown { /* 标记未满足或未执行核验。 */
  color: #667085; /* 使用中性色避免过度警示。 */
  background: #f0f2f5; /* 使用中性背景。 */
}

h3 { /* 设置论文标题。 */
  margin: 0.75rem 0 0.45rem; /* 与标签和书目信息分隔。 */
  color: #17324d; /* 使用深蓝保证阅读。 */
  font-family: Georgia, "Noto Serif SC", serif; /* 强化论文标题的出版感。 */
  font-size: clamp(1.05rem, 2vw, 1.28rem); /* 在不同屏幕保持合适字号。 */
  line-height: 1.4; /* 允许长标题舒适换行。 */
}

h3 a { /* 设置可访问论文标题链接。 */
  color: inherit; /* 保持标题配色。 */
  text-decoration-color: #a8c3d4; /* 使用柔和下划线。 */
  text-decoration-thickness: 1px; /* 避免下划线过重。 */
  text-underline-offset: 0.2em; /* 提升中英文标题可读性。 */
}

.bibliography { /* 横向展示作者、年份、venue 和引用数。 */
  display: flex; /* 使用弹性布局。 */
  flex-wrap: wrap; /* 窄屏允许元数据换行。 */
  gap: 0.35rem 0; /* 通过伪元素统一横向分隔。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #6b7f92; /* 使用次级文字色。 */
  font-size: 0.75rem; /* 保持书目信息紧凑。 */
  line-height: 1.6; /* 多行时保持可读。 */
}

.bibliography span:not(:last-child)::after { /* 在书目信息之间添加分隔点。 */
  margin: 0 0.5rem; /* 保持分隔点两侧空间。 */
  color: #becbd6; /* 弱化分隔符。 */
  content: "·"; /* 使用常见书目分隔符。 */
}

.recommendation { /* 突出证据支撑的推荐理由。 */
  display: grid; /* 纵向排列标签和理由。 */
  gap: 0.35rem; /* 分隔标签与正文。 */
  margin-top: 0.9rem; /* 与书目信息拉开层级。 */
  padding: 0.8rem 0.9rem; /* 提供理由背景留白。 */
  border-left: 3px solid #5d9ab4; /* 用品牌色标记推荐信息。 */
  border-radius: 0 0.65rem 0.65rem 0; /* 保持左侧证据线清晰。 */
  background: #f3f8fb; /* 使用浅蓝背景。 */
}

.recommendation-label { /* 标记推荐理由类型。 */
  color: #2e6f95; /* 使用品牌强调色。 */
  font-size: 0.65rem; /* 作为辅助标题。 */
  font-weight: 800; /* 保证标签辨识度。 */
  letter-spacing: 0.08em; /* 建立标签感。 */
}

.recommendation p { /* 设置推荐理由正文。 */
  margin: 0; /* 清除默认段落间距。 */
  color: #334e68; /* 使用高可读正文色。 */
  font-size: 0.82rem; /* 与摘要建立层级。 */
  line-height: 1.65; /* 提升中文长句可读性。 */
}

.evidence-list { /* 展示可回溯的原文证据。 */
  display: flex; /* 横向排列短证据片段。 */
  flex-wrap: wrap; /* 证据较长时自动换行。 */
  gap: 0.35rem; /* 分隔多个证据。 */
  margin: 0.7rem 0 0; /* 与推荐理由保持关联。 */
  padding: 0; /* 移除列表默认缩进。 */
  list-style: none; /* 使用胶囊而非列表圆点。 */
}

.evidence-list li { /* 设置单条证据片段。 */
  max-width: 100%; /* 防止长证据溢出卡片。 */
  padding: 0.28rem 0.5rem; /* 形成轻量证据胶囊。 */
  overflow: hidden; /* 隐藏超长单行内容。 */
  border: 1px solid #d8e5ec; /* 提供证据边界。 */
  border-radius: 0.45rem; /* 使用小圆角区别状态标签。 */
  color: #587184; /* 使用次级正文色。 */
  background: #fbfdfe; /* 保持证据低对比背景。 */
  font-size: 0.7rem; /* 控制证据信息密度。 */
  text-overflow: ellipsis; /* 超长证据显示省略号。 */
  white-space: nowrap; /* 保持胶囊单行。 */
}

.abstract-details { /* 提供按需展开的摘要。 */
  margin-top: 0.8rem; /* 与证据或书目信息分隔。 */
  color: #64748b; /* 使用次级文字色。 */
  font-size: 0.76rem; /* 摘要作为补充阅读信息。 */
}

.abstract-details summary { /* 设置摘要展开控件。 */
  width: fit-content; /* 仅让文字区域可点击。 */
  cursor: pointer; /* 告知用户可展开。 */
  color: #2e6f95; /* 使用品牌交互色。 */
  font-weight: 700; /* 提升操作可见性。 */
}

.abstract-details p { /* 设置展开后的摘要正文。 */
  margin: 0.6rem 0 0; /* 与摘要操作分隔。 */
  color: #52697d; /* 使用舒适正文色。 */
  line-height: 1.75; /* 提升长摘要阅读体验。 */
}

.paper-footer { /* 展示 DOI 和版本族等身份信息。 */
  display: flex; /* 横向排列身份标识。 */
  flex-wrap: wrap; /* 窄屏允许换行。 */
  gap: 0.75rem; /* 分隔不同标识。 */
  margin-top: 0.9rem; /* 与正文内容分隔。 */
  color: #95a4b2; /* 降低技术标识权重。 */
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace; /* 使用等宽字体方便辨认 ID。 */
  font-size: 0.62rem; /* 控制长标识占用空间。 */
}

@media (max-width: 560px) { /* 调整手机论文卡布局。 */
  .paper-card { /* 将排名压缩为更窄列。 */
    grid-template-columns: 3.3rem minmax(0, 1fr); /* 为正文保留更多空间。 */
  }

  .paper-content { /* 缩小手机卡片内边距。 */
    padding: 1rem; /* 保持内容舒适且不拥挤。 */
  }
}
</style>
