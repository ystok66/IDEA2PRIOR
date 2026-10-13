

from importlib import import_module

_EXPORTS = {'disable_logging': ('rag.logger', 'disable_logging'), 'enable_logging': ('rag.logger', 'enable_logging'), 'set_log_level': ('rag.logger', 'set_log_level')}
__all__ = list(_EXPORTS)

def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module, __name__), attribute)
    globals()[name] = value
    return value
