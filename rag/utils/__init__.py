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

_EXPORTS = {'AgentOpsMeta': ('.commons', 'AgentOpsMeta'), 'agentops_decorator': ('.commons', 'agentops_decorator'), 'api_keys_required': ('.commons', 'api_keys_required'), 'check_server_running': ('.commons', 'check_server_running'), 'create_chunks': ('.commons', 'create_chunks'), 'dependencies_required': ('.commons', 'dependencies_required'), 'download_tasks': ('.commons', 'download_tasks'), 'func_string_to_callable': ('.commons', 'func_string_to_callable'), 'get_first_int': ('.commons', 'get_first_int'), 'get_prompt_template_key_words': ('.commons', 'get_prompt_template_key_words'), 'get_pydantic_major_version': ('.commons', 'get_pydantic_major_version'), 'get_pydantic_object_schema': ('.commons', 'get_pydantic_object_schema'), 'get_system_information': ('.commons', 'get_system_information'), 'get_task_list': ('.commons', 'get_task_list'), 'handle_http_error': ('.commons', 'handle_http_error'), 'is_docker_running': ('.commons', 'is_docker_running'), 'json_to_function_code': ('.commons', 'json_to_function_code'), 'print_text_animated': ('.commons', 'print_text_animated'), 'text_extract_from_web': ('.commons', 'text_extract_from_web'), 'to_pascal': ('.commons', 'to_pascal'), 'track_agent': ('.commons', 'track_agent'), 'Constants': ('.constants', 'Constants'), 'get_pydantic_model': ('.response_format', 'get_pydantic_model'), 'AnthropicTokenCounter': ('.token_counting', 'AnthropicTokenCounter'), 'BaseTokenCounter': ('.token_counting', 'BaseTokenCounter'), 'GeminiTokenCounter': ('.token_counting', 'GeminiTokenCounter'), 'LiteLLMTokenCounter': ('.token_counting', 'LiteLLMTokenCounter'), 'MistralTokenCounter': ('.token_counting', 'MistralTokenCounter'), 'OpenAITokenCounter': ('.token_counting', 'OpenAITokenCounter'), 'get_model_encoding': ('.token_counting', 'get_model_encoding')}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
