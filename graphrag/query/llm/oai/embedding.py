# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""OpenAI Embedding model implementation."""

import asyncio
from collections.abc import Callable
from typing import Any

import numpy as np
import tiktoken
from tenacity import (
    AsyncRetrying,
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from graphrag.query.llm.base import BaseTextEmbedding
from graphrag.query.llm.oai.base import OpenAILLMImpl
from graphrag.query.llm.oai.typing import (
    OPENAI_RETRY_ERROR_TYPES,
    OpenaiApiType,
)
from graphrag.query.llm.text_utils import chunk_text
from graphrag.query.progress import StatusReporter
from graphrag.llm.extra.factories import is_valid_llm_type, use_embeddings


class OpenAIEmbedding(BaseTextEmbedding, OpenAILLMImpl):
    """Wrapper for OpenAI Embedding models."""

    def __init__(
        self,
        api_key: str | None = None,
        azure_ad_token_provider: Callable | None = None,
        model: str = "text-embedding-3-small",
        deployment_name: str | None = None,
        api_base: str | None = None,
        api_version: str | None = None,
        api_type: OpenaiApiType = OpenaiApiType.OpenAI,
        organization: str | None = None,
        encoding_name: str = "cl100k_base",
        max_tokens: int = 8191,
        max_retries: int = 10,
        request_timeout: float = 180.0,
        retry_error_types: tuple[type[BaseException]] = OPENAI_RETRY_ERROR_TYPES,  # type: ignore
        reporter: StatusReporter | None = None,
    ):
        OpenAILLMImpl.__init__(
            self=self,
            api_key=api_key,
            azure_ad_token_provider=azure_ad_token_provider,
            deployment_name=deployment_name,
            api_base=api_base,
            api_version=api_version,
            api_type=api_type,  # type: ignore
            organization=organization,
            max_retries=max_retries,
            request_timeout=request_timeout,
            reporter=reporter,
        )

        self.model = model
        self.encoding_name = encoding_name
        self.max_tokens = max_tokens
        self.token_encoder = tiktoken.get_encoding(self.encoding_name)
        self.retry_error_types = retry_error_types

    def embed(self, text: str, **kwargs: Any) -> list[float]:
        """
        Embed text using OpenAI Embedding's sync function.

        For text longer than max_tokens, chunk texts into max_tokens, embed each chunk, then combine using weighted average.
        Please refer to: https://github.com/openai/openai-cookbook/blob/main/examples/Embedding_long_inputs.ipynb
        """
        # 核心逻辑
        # query时在这里调用embedding模型
        # 调试时在这里打断点

        # 将text进行tokenize，如果超过max_tokens，则进行分块
        token_chunks = chunk_text(
            text=text, token_encoder=self.token_encoder, max_tokens=self.max_tokens
        )
        chunk_embeddings = []
        chunk_lens = []
        # embedding每一个分块
        for chunk in token_chunks:
            try:
                # 注意这里的chunk_len是原始文本的str len
                embedding, chunk_len = self._embed_with_retry(chunk, **kwargs)
                chunk_embeddings.append(embedding)
                chunk_lens.append(chunk_len)
            # TODO: catch a more specific exception
            except Exception as e:  # noqa BLE001
                self._reporter.error(
                    message="Error embedding chunk",
                    details={self.__class__.__name__: str(e)},
                )

                continue
        # 基于分块长度进行加权平均
        chunk_embeddings = np.average(chunk_embeddings, axis=0, weights=chunk_lens)
        # 除以L2范式进行归一化
        chunk_embeddings = chunk_embeddings / np.linalg.norm(chunk_embeddings)
        return chunk_embeddings.tolist()

    async def aembed(self, text: str, **kwargs: Any) -> list[float]:
        """
        Embed text using OpenAI Embedding's async function.

        For text longer than max_tokens, chunk texts into max_tokens, embed each chunk, then combine using weighted average.
        """
        token_chunks = chunk_text(
            text=text, token_encoder=self.token_encoder, max_tokens=self.max_tokens
        )
        chunk_embeddings = []
        chunk_lens = []
        embedding_results = await asyncio.gather(*[
            self._aembed_with_retry(chunk, **kwargs) for chunk in token_chunks
        ])
        embedding_results = [result for result in embedding_results if result[0]]
        chunk_embeddings = [result[0] for result in embedding_results]
        chunk_lens = [result[1] for result in embedding_results]
        chunk_embeddings = np.average(chunk_embeddings, axis=0, weights=chunk_lens)  # type: ignore
        chunk_embeddings = chunk_embeddings / np.linalg.norm(chunk_embeddings)
        return chunk_embeddings.tolist()

    def _embed_with_retry(
        self, text: str | tuple, **kwargs: Any
    ) -> tuple[list[float], int]:
        # 核心逻辑
        # 调用embedding模型，编码一个长度不超过上限的文本片段
        embeddings = None
        llm_type, *models = self.model.split('.')
        # 如果是预设的embedding模型
        if is_valid_llm_type(llm_type):
            embeddings = use_embeddings(llm_type, model='.'.join(models))

        try:
            retryer = Retrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential_jitter(max=10),
                reraise=True,
                retry=retry_if_exception_type(self.retry_error_types),
            )
            for attempt in retryer:
                with attempt:
                    if embeddings is not None:
                        if isinstance(text, tuple):
                            text = self.token_encoder.decode(text)
                        # 调用预设的embedding模型
                        return (embeddings.embed_query(text), len(text))
                    # 如果不是预设的embedding模型
                    # 通过OpenAI接口调用
                    embedding = (
                        self.sync_client.embeddings.create(  # type: ignore
                            input=text,
                            model=self.model,
                            **kwargs,  # type: ignore
                        )
                        .data[0]
                        .embedding
                        or []
                    )
                    return (embedding, len(text))
        except RetryError as e:
            self._reporter.error(
                message="Error at embed_with_retry()",
                details={self.__class__.__name__: str(e)},
            )
            return ([], 0)
        else:
            # TODO: why not just throw in this case?
            return ([], 0)

    async def _aembed_with_retry(
        self, text: str | tuple, **kwargs: Any
    ) -> tuple[list[float], int]:

        embeddings = None
        llm_type, *models = self.model.split('.')
        if is_valid_llm_type(llm_type):
            embeddings = use_embeddings(llm_type, model='.'.join(models))

        try:
            retryer = AsyncRetrying(
                stop=stop_after_attempt(self.max_retries),
                wait=wait_exponential_jitter(max=10),
                reraise=True,
                retry=retry_if_exception_type(self.retry_error_types),
            )
            async for attempt in retryer:
                with attempt:
                    if embeddings is not None:
                        return await embeddings.aembed_query(text), len(text)
                    embedding = (
                        await self.async_client.embeddings.create(  # type: ignore
                            input=text,
                            model=self.model,
                            **kwargs,  # type: ignore
                        )
                    ).data[0].embedding or []
                    return (embedding, len(text))
        except RetryError as e:
            self._reporter.error(
                message="Error at embed_with_retry()",
                details={self.__class__.__name__: str(e)},
            )
            return ([], 0)
        else:
            # TODO: why not just throw in this case?
            return ([], 0)
