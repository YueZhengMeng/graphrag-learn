# Copyright (c) 2024 Microsoft Corporation.
# Licensed under the MIT License

"""A text-completion based LLM."""

import logging

from typing_extensions import Unpack

from graphrag.llm.base import BaseLLM
from graphrag.llm.types import (
    CompletionInput,
    CompletionOutput,
    LLMInput,
)

from .openai_configuration import OpenAIConfiguration
from .types import OpenAIClientTypes
from .utils import get_completion_llm_args
from ..extra.factories import use_llm, is_valid_llm_type

log = logging.getLogger(__name__)


class OpenAICompletionLLM(BaseLLM[CompletionInput, CompletionOutput]):
    """A text-completion based LLM."""

    _client: OpenAIClientTypes
    _configuration: OpenAIConfiguration

    def __init__(self, client: OpenAIClientTypes, configuration: OpenAIConfiguration):
        self.client = client
        self.configuration = configuration

    async def _execute_llm(
        self,
        input: CompletionInput,
        **kwargs: Unpack[LLMInput],
    ) -> CompletionOutput | None:
        args = get_completion_llm_args(
            kwargs.get("model_parameters"), self.configuration
        )
        model = self.configuration.lookup('model', '')
        llm_type, *models = model.split('.')
        if is_valid_llm_type(llm_type):
            llm = use_llm(llm_type, model='.'.join(models))
            content = await llm.ainvoke(input)
            return content

        completion = self.client.completions.create(prompt=input, **args)
        return completion.choices[0].text
