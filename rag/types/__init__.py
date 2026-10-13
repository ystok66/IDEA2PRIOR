# ========= Copyright 2023-2024 @ CAMEL-AI.org. All Rights Reserved. =========
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ========= Copyright 2023-2024 @ CAMEL-AI.org. All Rights Reserved. =========

from importlib import import_module

_EXPORTS = {'AudioModelType': ('.enums', 'AudioModelType'), 'EmbeddingModelType': ('.enums', 'EmbeddingModelType'), 'HuggingFaceRepoType': ('.enums', 'HuggingFaceRepoType'), 'ModelPlatformType': ('.enums', 'ModelPlatformType'), 'ModelType': ('.enums', 'ModelType'), 'OpenAIBackendRole': ('.enums', 'OpenAIBackendRole'), 'OpenAIImageType': ('.enums', 'OpenAIImageType'), 'OpenAIVisionDetailType': ('.enums', 'OpenAIVisionDetailType'), 'OpenAPIName': ('.enums', 'OpenAPIName'), 'RoleType': ('.enums', 'RoleType'), 'StorageType': ('.enums', 'StorageType'), 'TaskType': ('.enums', 'TaskType'), 'TerminationMode': ('.enums', 'TerminationMode'), 'VectorDistance': ('.enums', 'VectorDistance'), 'VoiceType': ('.enums', 'VoiceType'), 'NOT_GIVEN': ('.openai_types', 'NOT_GIVEN'), 'ChatCompletion': ('.openai_types', 'ChatCompletion'), 'ChatCompletionAssistantMessageParam': ('.openai_types', 'ChatCompletionAssistantMessageParam'), 'ChatCompletionChunk': ('.openai_types', 'ChatCompletionChunk'), 'ChatCompletionMessage': ('.openai_types', 'ChatCompletionMessage'), 'ChatCompletionMessageParam': ('.openai_types', 'ChatCompletionMessageParam'), 'ChatCompletionMessageToolCall': ('.openai_types', 'ChatCompletionMessageToolCall'), 'ChatCompletionSystemMessageParam': ('.openai_types', 'ChatCompletionSystemMessageParam'), 'ChatCompletionToolMessageParam': ('.openai_types', 'ChatCompletionToolMessageParam'), 'ChatCompletionUserMessageParam': ('.openai_types', 'ChatCompletionUserMessageParam'), 'Choice': ('.openai_types', 'Choice'), 'CompletionUsage': ('.openai_types', 'CompletionUsage'), 'NotGiven': ('.openai_types', 'NotGiven'), 'ParsedChatCompletion': ('.openai_types', 'ParsedChatCompletion'), 'UnifiedModelType': ('.unified_model_type', 'UnifiedModelType')}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
