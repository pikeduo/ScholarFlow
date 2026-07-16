"""封装 DeepSeek Chat Completions 的论文核验与结构化响应解析。"""

import json  # 构造 UTF-8 友好的提示正文并解析 JSON 模型输出。
from typing import Protocol  # 定义可由测试替身或其他 LLM 供应商实现的边界。

import httpx  # 复用项目已有异步 HTTP 客户端访问 DeepSeek 官方接口。
from pydantic import BaseModel, Field, ValidationError  # 严格校验供应商返回的 JSON 内容。

from backend.app.core.config import Settings, settings  # 从集中配置读取端点、密钥、模型和超时。
from backend.app.models.llm_ranking import LlmAssessmentBatch, LlmPaperAssessment  # 返回供应商无关的结构化核验契约。
from backend.app.models.paper import PaperRecord  # 接收 Cross Encoder 截断后的论文候选。
from backend.app.models.query_intent import QueryIntent  # 接收统一查询意图及其硬软约束。
from backend.app.core.deepseek_pricing import estimate_deepseek_cost_or_zero  # 从无服务聚合副作用的基础模块读取费用估算，避免循环导入。


class LlmAssessmentError(RuntimeError):
    """表示 LLM 配置、网络、响应或结构化输出不可用的已净化错误。"""


class PaperAssessmentClient(Protocol):
    """定义 LLM 论文核验客户端的可替换异步协议。"""

    async def assess(self, query: QueryIntent, papers: list[PaperRecord]) -> LlmAssessmentBatch:
        """核验候选论文并返回逐篇相关性、约束状态、证据和理由。"""
        ...


class _AssessmentPayload(BaseModel):
    """校验 DeepSeek JSON 输出的顶层对象。"""

    assessments: list[LlmPaperAssessment] = Field(default_factory=list)  # 要求模型将逐篇结果放入固定字段。


class DeepSeekPaperAssessmentClient:
    """使用 DeepSeek JSON Output 一次性核验一批候选论文。"""

    def __init__(self, config: Settings = settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """保存集中配置和可选测试传输层，构造阶段不发起网络请求。"""
        self._config = config  # 延迟到实际调用时读取和校验敏感密钥。
        self._transport = transport  # 允许单元测试注入 MockTransport 而不访问网络。

    async def assess(self, query: QueryIntent, papers: list[PaperRecord]) -> LlmAssessmentBatch:
        """调用 DeepSeek Chat Completions 并转换为供应商无关核验结果。

        异常：
            LlmAssessmentError：配置缺失、网络失败、非成功响应或 JSON 结构无效。
        """
        if not papers:  # 空候选不应消耗 API Token。
            return LlmAssessmentBatch(assessments=[], model_name=self._config.deepseek_model)  # 返回稳定空批次。
        try:  # 在请求边界统一净化配置错误，避免向上层暴露敏感配置对象。
            api_key = self._config.require_deepseek_api_key()  # 仅在真实调用前解封装密钥。
        except ValueError as exc:  # 缺少密钥属于可降级的模型配置问题。
            raise LlmAssessmentError("DeepSeek API 未配置") from exc  # 返回不含环境内容的稳定异常。
        request_body = {  # 按官方 Chat Completions JSON Output 契约构造请求。
            "model": self._config.deepseek_model,  # 默认使用成本较低的 Flash 模型。
            "messages": [  # 用系统规则约束证据边界，并在用户消息中提供结构化候选。
                {"role": "system", "content": _SYSTEM_PROMPT},  # 明确要求只输出 JSON 且不得虚构证据。
                {"role": "user", "content": _build_user_prompt(query, papers)},  # 提供查询约束与公开论文元数据。
            ],
            "response_format": {"type": "json_object"},  # 启用官方 JSON Output 模式。
            "thinking": {"type": "disabled"},  # 精排任务使用非思考模式以控制延迟与成本。
            "temperature": 0.0,  # 降低同批候选重复核验时的随机波动。
            "max_tokens": self._config.deepseek_max_output_tokens,  # 防止输出无界增长并为完整 JSON 保留空间。
            "stream": False,  # 当前服务需要完整 JSON 后统一校验。
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}  # 密钥仅进入请求头且不写日志。
        try:  # 将网络与 HTTP 故障统一转换为适配器错误。
            async with httpx.AsyncClient(base_url=str(self._config.deepseek_api_base_url).rstrip("/"), timeout=self._config.deepseek_llm_timeout_seconds, transport=self._transport) as client:  # 仅使用论文核验的小批次超时，避免影响查询规划。
                response = await client.post("/chat/completions", headers=headers, json=request_body)  # 调用 OpenAI 兼容聊天端点。
                response.raise_for_status()  # 非成功响应不得进入业务解析。
                response_data = response.json()  # 仅在内存中解析供应商响应，不记录原文。
        except (httpx.HTTPError, ValueError) as exc:  # 覆盖传输、状态码与外层 JSON 解析失败。
            raise LlmAssessmentError("DeepSeek 论文核验调用失败") from exc  # 隐藏 URL、响应正文和鉴权信息。
        try:  # 对嵌套响应和模型生成内容执行两层结构校验。
            content = response_data["choices"][0]["message"]["content"]  # 读取非流式首个模型输出。
            payload = _parse_assessment_payload(content)  # 严格校验固定 assessments JSON 对象，并仅修复可判定的模型转义瑕疵。
            usage = response_data.get("usage") or {}  # 兼容供应商未返回 Token 统计的情况。
            model_name = str(response_data.get("model") or self._config.deepseek_model)  # 优先记录实际响应模型。
            prompt_tokens = int(usage.get("prompt_tokens") or 0)  # 保存供应商报告的完整输入 Token 数量。
            completion_tokens = int(usage.get("completion_tokens") or 0)  # 保存供应商报告的完整输出 Token 数量。
            cost_estimate = estimate_deepseek_cost_or_zero(model_name, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, prompt_cache_hit_tokens=int(usage.get("prompt_cache_hit_tokens") or 0), prompt_cache_miss_tokens=int(usage.get("prompt_cache_miss_tokens") or 0))  # 在响应仍保留缓存 usage 时计算费用。
            return LlmAssessmentBatch(  # 转换为供应商无关批次供服务层处理证据和排序。
                assessments=payload.assessments,  # 返回已通过字段范围校验的逐篇结果。
                model_name=model_name,  # 保存实际或配置模型名。
                prompt_tokens=prompt_tokens,  # 缺失统计时安全回退为零。
                completion_tokens=completion_tokens,  # 缺失统计时安全回退为零。
                estimated_cost_cny=cost_estimate.cost_cny,  # 将实际 usage 的人民币估算交给运行快照累计。
                peak_pricing_applied=cost_estimate.peak_pricing_applied,  # 保留工作时间两倍费率审计标记。
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:  # 覆盖缺字段、空选择和模型 JSON 不合法。
            raise LlmAssessmentError("DeepSeek 返回了无效的论文核验结果") from exc  # 不泄露可能包含用户查询的响应正文。


_SYSTEM_PROMPT = """你是学术论文结果核验器。必须只输出 JSON 对象，不输出 Markdown 或思维过程。\n
逐篇判断论文与查询的相关性及硬约束是否满足。只能使用输入中的标题、摘要、关键词、作者、年份、venue 和论文类型，不得补充外部知识。\n
evidence 必须是输入论文公开元数据中逐字出现的短片段；证据不足时 constraint_status 使用 uncertain。\n
JSON 字符串中的引号、反斜杠和换行必须按 JSON 规范转义；不得在理由或证据内输出未转义的双引号。\n
输出格式：{\"assessments\":[{\"paper_id\":\"原始ID\",\"relevance_score\":0.0,\"constraint_status\":\"satisfied|uncertain|not_satisfied\",\"evidence\":[\"原文片段\"],\"recommendation_reason\":\"简短中文理由\"}]}。"""  # 明确 JSON、证据与状态边界。


def _parse_assessment_payload(content: object) -> _AssessmentPayload:
    """严格解析论文核验对象，并仅对可确定的 JSON 转义错误执行一次本地修复。

    参数：
        content：供应商响应中首个消息的完整文本。
    返回：
        _AssessmentPayload：通过领域字段校验的核验对象。
    异常：
        TypeError：模型内容不是文本时抛出。
        ValidationError：原文与受限修复文本均不符合 JSON 或领域契约时抛出。
    """
    if not isinstance(content, str):  # Chat Completions 非文本内容不具备可安全修复的语义。
        raise TypeError("DeepSeek 论文核验内容必须为文本")  # 交由适配器边界统一净化为公共错误。
    try:  # 优先保留完全合法的供应商输出，避免无谓修改模型文本。
        return _AssessmentPayload.model_validate_json(content)  # 使用 Pydantic 同时完成 JSON 和领域字段校验。
    except ValidationError as original_error:  # 仅在严格解析失败后尝试有限的格式修复。
        repaired_content = _repair_model_json(content)  # 只处理 Markdown 围栏、未转义引号、控制字符和无效反斜杠。
        if repaired_content == content:  # 未发现可判定的格式瑕疵时保留原始失败原因。
            raise  # 防止把任意模型语义错误伪装成可恢复格式错误。
        try:  # 修复后仍必须通过与原文相同的完整领域契约校验。
            return _AssessmentPayload.model_validate_json(repaired_content)  # 不接受仅能解析但字段不合格的内容。
        except ValidationError:  # 修复失败说明内容可能截断或语义结构错误。
            raise original_error  # 返回原始校验异常，避免暴露模型原文。


def _repair_model_json(content: str) -> str:
    """修复 JSON Output 模式偶发的可判定转义瑕疵，不猜测或补全业务字段。"""
    candidate = _strip_json_markdown_fence(content)  # 先移除与 JSON Object 模式不兼容的完整 Markdown 围栏。
    if not candidate.startswith("{"):  # 非对象文本无法安全推断其业务结构。
        return candidate  # 保持原状并由严格解析拒绝。
    repaired: list[str] = []  # 按字符重建仅在 JSON 字符串内部做最小改动的文本。
    in_string = False  # 跟踪当前位置是否位于 JSON 双引号字符串中。
    escaped = False  # 跟踪前一个字符是否为尚待处理的反斜杠。
    for index, character in enumerate(candidate):  # 单次扫描避免正则误改嵌套对象或数组结构。
        if not in_string:  # JSON 结构区不应修改任何标点或字段语义。
            repaired.append(character)  # 原样保留大括号、数组、冒号、逗号与数值。
            if character == '"':  # 非字符串区遇到双引号即进入键名或字符串值。
                in_string = True  # 后续仅修复当前字符串内的编码瑕疵。
            continue  # 结构区当前字符处理完成。
        if escaped:  # 当前字符紧随反斜杠，需要验证是否为合法 JSON 转义。
            if _is_valid_json_escape(candidate, index):  # 保留标准转义或完整 Unicode 转义。
                repaired.append(character)  # 原样保留已合法的转义字符。
            else:  # 模型常把 LaTeX 或路径反斜杠直接写入 JSON 字符串。
                repaired.append("\\")  # 补充一个反斜杠，使原反斜杠成为字面量。
                repaired.append(character)  # 保留原始字符，不丢失论文元数据文本。
            escaped = False  # 当前反斜杠序列已处理完毕。
            continue  # 继续检查下一个字符。
        if character == "\\":  # 记录合法性尚待下一个字符判断的反斜杠。
            repaired.append(character)  # 先保留原始反斜杠，必要时在下一步补充转义。
            escaped = True  # 标记下一个字符属于同一转义序列。
            continue  # 不把转义后的引号错误当作字符串结束。
        if character == '"':  # 字符串内双引号可能是结束符，也可能是模型遗漏转义的字面量。
            if _is_json_string_closure(candidate, index):  # 仅在后续结构符号明确时将其视为结束符。
                repaired.append(character)  # 原样保留真正的字符串闭合引号。
                in_string = False  # 回到 JSON 结构区。
            else:  # 后续仍是同一自然语言字符串时，该引号只能是未转义字面量。
                repaired.append('\\"')  # 转义字面量引号而不改变其展示文本。
            continue  # 当前引号已处理完毕。
        if ord(character) < 0x20:  # JSON 字符串不允许直接包含换行、制表符等控制字符。
            repaired.append(json.dumps(character)[1:-1])  # 使用标准 JSON 转义保留原控制字符语义。
            continue  # 控制字符已安全编码。
        repaired.append(character)  # 普通 Unicode 文本不做任何变换。
    if escaped:  # 文本末尾孤立反斜杠会使 JSON 失效，但可无损表示为字面量。
        repaired.append("\\")  # 补足第二个反斜杠形成合法 JSON 字符串内容。
    return "".join(repaired)  # 返回仍需通过 Pydantic 完整校验的候选文本。


def _strip_json_markdown_fence(content: str) -> str:
    """仅移除完整包裹 JSON 的 Markdown 围栏，避免截取或猜测自由文本中的对象。"""
    stripped = content.strip()  # 忽略模型输出外围不影响 JSON 语义的空白。
    if not stripped.startswith("```"):  # 非围栏输出无需额外处理。
        return stripped  # 同时规范化外围空白，便于后续判断。
    first_line_end = stripped.find("\n")  # Markdown 围栏第一行可能携带 json 语言标记。
    closing_fence_start = stripped.rfind("```")  # 只接受最后一个围栏作为完整闭合边界。
    if first_line_end < 0 or closing_fence_start <= first_line_end:  # 缺少完整开闭围栏时不截取不可信内容。
        return stripped  # 交由严格解析拒绝截断或非 JSON 输出。
    return stripped[first_line_end + 1:closing_fence_start].strip()  # 返回围栏内唯一候选 JSON 对象。


def _is_valid_json_escape(content: str, index: int) -> bool:
    """判断反斜杠后的字符能否构成标准 JSON 转义序列。"""
    character = content[index]  # 读取当前反斜杠后的候选字符。
    if character in {'"', "\\", "/", "b", "f", "n", "r", "t"}:  # 覆盖 JSON 规定的单字符转义。
        return True  # 当前序列可直接保留。
    if character != "u" or index + 4 >= len(content):  # Unicode 转义必须带有后续四位十六进制字符。
        return False  # 其他形式应将反斜杠转义为字面量。
    return all(digit in "0123456789abcdefABCDEF" for digit in content[index + 1:index + 5])  # 严格验证四位 Unicode 十六进制编码。


def _is_json_string_closure(content: str, quote_index: int) -> bool:
    """仅在后续语法明确时判定字符串内双引号为 JSON 闭合符。"""
    next_index = _skip_json_whitespace(content, quote_index + 1)  # 跳过闭合引号后允许出现的结构空白。
    if next_index >= len(content):  # 文本结束仅可能是 JSON 字符串闭合后的截断边界。
        return True  # 交由外层 JSON 解析确认对象是否完整。
    next_character = content[next_index]  # 读取紧邻字符串后的首个非空白字符。
    if next_character in {":", "}", "]"}:  # 键名或对象、数组末尾可直接闭合字符串。
        return True  # 当前引号属于结构闭合。
    if next_character != ",":  # 自然语言文字、句号等不能紧跟合法 JSON 字符串结束符。
        return False  # 将当前引号修复为字符串字面量。
    after_comma_index = _skip_json_whitespace(content, next_index + 1)  # 逗号后必须出现下一项或容器结束。
    if after_comma_index >= len(content):  # 末尾逗号虽然仍非法，但当前字符串闭合可被确定。
        return True  # 不把最后一个字符串引号误转义。
    return content[after_comma_index] in {'"', "}", "]"}  # 仅当逗号后结构明确时接受当前引号为闭合。


def _skip_json_whitespace(content: str, start_index: int) -> int:
    """返回从给定位置起第一个非 JSON 空白字符的位置。"""
    index = start_index  # 从调用方提供的位置开始向后扫描。
    while index < len(content) and content[index] in " \t\r\n":  # JSON 结构区只允许这四类空白。
        index += 1  # 跳过一个已允许的空白字符。
    return index  # 返回首个结构字符或文本末尾位置。


def _build_user_prompt(query: QueryIntent, papers: list[PaperRecord]) -> str:
    """将查询约束和论文公开元数据序列化为可复现的 JSON 提示。"""
    query_payload = {  # 只发送核验所需字段，不发送运行状态、密钥或补充网页内容。
        "query": query.normalized_query,  # 使用规范化查询减少无意义差异。
        "topics": query.research_topics,  # 提供研究主题。
        "methods": query.methods,  # 提供目标方法。
        "tasks": query.tasks,  # 提供科研任务。
        "datasets": query.datasets,  # 提供数据集约束。
        "authors": query.authors,  # 提供作者硬约束。
        "institutions": query.institutions,  # 提供机构硬约束。
        "venues": query.venues,  # 提供 venue 硬约束。
        "paper_types": query.paper_types,  # 提供论文类型硬约束。
        "year_range": query.year_range,  # 提供年份闭区间。
        "must_include": query.must_include,  # 提供必须条件。
        "should_include": query.should_include,  # 提供软偏好。
        "exclude": query.exclude,  # 提供排除条件。
    }
    paper_payloads = [  # 为每篇候选建立只含公开元数据的核验对象。
        {
            "paper_id": paper.paper_id,  # 保持模型输出可与输入稳定关联。
            "title": paper.title,  # 提供最强主题证据。
            "abstract": paper.abstract,  # 提供方法、任务和数据集证据。
            "keywords": paper.keywords,  # 补充摘要缺失时的主题证据。
            "authors": [{"name": author.name, "institution": author.institution} for author in paper.authors],  # 提供作者和机构核验字段。
            "year": paper.year,  # 提供确定性年份元数据。
            "venue": paper.venue,  # 提供会议或期刊信息。
            "paper_type": paper.paper_type,  # 提供统一论文类型。
        }
        for paper in papers  # 保持 Cross Encoder 顺序便于模型审阅。
    ]
    return json.dumps({"query_intent": query_payload, "papers": paper_payloads}, ensure_ascii=False, separators=(",", ":"))  # 使用紧凑 UTF-8 JSON 控制输入 Token。
