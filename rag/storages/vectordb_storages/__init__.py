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

_EXPORTS = {'BaseVectorStorage': ('.base', 'BaseVectorStorage'), 'VectorDBQuery': ('.base', 'VectorDBQuery'), 'VectorDBQueryResult': ('.base', 'VectorDBQueryResult'), 'VectorDBStatus': ('.base', 'VectorDBStatus'), 'VectorRecord': ('.base', 'VectorRecord'), 'MilvusStorage': ('.milvus', 'MilvusStorage'), 'QdrantStorage': ('.qdrant', 'QdrantStorage')}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
