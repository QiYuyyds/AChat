# extractors.llm — LLM-based graph extractor with json_repair + schema support
from __future__ import annotations

import asyncio
import logging

from json_repair import loads as json_repair_loads

from ..types import ChunkRef, ExtractResult
from .base import GraphExtractor

logger = logging.getLogger(__name__)

# LLM callback signature: (system_prompt, user_msg) -> str
LLMFn = ...  # type alias — re-exported from extractor.py for compatibility


DEFAULT_TRIPLE_EXTRACTION_PROMPT = """你是一个信息抽取专家。从给定文本中抽取命名实体和实体间关系。

实体类型（label 字段只能用以下值）：
- Person（人物）
- Organization（组织/公司/机构）
- Location（地点/地区）
- Concept（概念/技术/思想）
- Event（事件）
- Product（产品/工具）
- Unknown（其他）

关系类型（label 字段只能用以下值）：
- RELATES_TO（相关）
- PART_OF（属于/是...的一部分）
- CAUSES（导致/引发）
- DESCRIBES（描述/介绍）
- MENTIONS（提及）
- WORKS_FOR（工作于）
- LOCATED_IN（位于）

输出格式（只输出 JSON，不加任何说明）：
{
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型"
    }
  ]
}

如果文本中没有可抽取的实体，输出 {"relations":[]}"""

SCHEMA_INSTRUCTION = "\n抽取 Schema 约束：\n{schema}\n"


class LLMGraphExtractor(GraphExtractor):
    """LLM-based graph extractor using injected llm_fn callback.

    Options:
        llm_fn: callable (system_prompt, user_text) -> str
        schema: optional schema constraint string appended to prompt
        model_params: optional dict (stored but not used with sync llm_fn)
        concurrency_count: optional int for batch concurrency (default 5)
    """

    def __init__(self, options: dict):
        self._llm_fn = options.get("llm_fn")
        self._schema = str(options.get("schema") or "").strip()
        self._model_params = options.get("model_params") or {}
        self._concurrency_count = int(options.get("concurrency_count") or 5)

    def _build_system_prompt(self) -> str:
        prompt = DEFAULT_TRIPLE_EXTRACTION_PROMPT
        if self._schema:
            prompt = f"{prompt}\n{SCHEMA_INSTRUCTION.format(schema=self._schema)}"
        return prompt

    def _build_user_text(self, text: str) -> str:
        return f"文本：\n{text}"

    def extract(self, text: str, options: dict | None = None) -> ExtractResult:
        """Extract entities and relations from a single text chunk.

        Returns empty ExtractResult (not raises) when:
        - llm_fn is None or not callable
        - text is empty or whitespace-only
        - LLM call raises exception
        - JSON parsing fails
        - Parsed result has unexpected structure
        """
        if self._llm_fn is None or not callable(self._llm_fn):
            return ExtractResult()

        if not text or not text.strip():
            return ExtractResult()

        system_prompt = self._build_system_prompt()
        user_text = self._build_user_text(text)

        try:
            raw = self._llm_fn(system_prompt, user_text)
        except Exception as e:
            logger.warning("LLM graph extraction call failed: %s", e)
            return ExtractResult()

        if not raw or not raw.strip():
            return ExtractResult()

        try:
            parsed = json_repair_loads(raw)
        except Exception as e:
            logger.warning("json_repair parsing failed: %s (raw: %.100s)", e, raw)
            return ExtractResult()

        if not isinstance(parsed, dict):
            return ExtractResult()

        return GraphExtractor.normalize_raw_dict(parsed)

    async def extract_batch(
        self,
        chunks: list[ChunkRef],
        options: dict | None = None,
    ) -> list[ExtractResult]:
        """Concurrent batch extraction with semaphore control.

        Uses asyncio.Semaphore for concurrency. Single chunk failures return
        empty ExtractResult without interrupting other chunks.
        """
        if not chunks:
            return []

        concurrency = self._concurrency_count
        if options and "concurrency" in options:
            concurrency = int(options["concurrency"])
        if concurrency < 1:
            concurrency = 1

        sem = asyncio.Semaphore(concurrency)

        async def _extract_one(chunk: ChunkRef) -> ExtractResult:
            async with sem:
                return self.extract(chunk.content)

        results = await asyncio.gather(
            *(_extract_one(c) for c in chunks),
            return_exceptions=False,
        )
        return list(results)
