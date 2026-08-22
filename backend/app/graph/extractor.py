# extractor — 向后兼容包装器，委托给 LLMGraphExtractor
import logging
from collections.abc import Callable

from .extractors.llm import LLMGraphExtractor
from .types import ChunkRef, ExtractResult

logger = logging.getLogger(__name__)

# LLM 回调签名：(system_prompt, user_msg) -> str
LLMFn = Callable[[str, str], str]


class Extractor:
    """向后兼容包装器，内部委托给 LLMGraphExtractor。

    保留 __init__(self, llm_fn) 签名，调用方无需修改。
    新代码应直接使用 GraphExtractorFactory.create("llm", {"llm_fn": ...})。
    """

    def __init__(self, llm_fn: LLMFn | None):
        self._delegate = LLMGraphExtractor({"llm_fn": llm_fn}) if llm_fn else None

    def extract(self, text: str) -> ExtractResult:
        """从单段文本中抽取实体和关系；LLM 不可用或解析失败时返回空结果（不抛异常）。"""
        if not self._delegate:
            return ExtractResult()
        return self._delegate.extract(text)

    async def extract_batch(
        self,
        chunks: list[ChunkRef],
        *,
        concurrency: int = 5,
    ) -> list[ExtractResult]:
        """批量并发抽取多个 chunk 的实体关系。

        内部委托给 LLMGraphExtractor.extract_batch，用 asyncio.Semaphore 控制并发。
        单个 chunk 抽取失败返回空 ExtractResult（不中断其他 chunk）。
        """
        if not self._delegate or not chunks:
            return []
        return await self._delegate.extract_batch(
            chunks, options={"concurrency": concurrency},
        )
