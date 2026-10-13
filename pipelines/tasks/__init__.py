"""Lazy task exports avoid importing model clients during config validation."""
from importlib import import_module

__all__ = ["run_prior_art_check", "run_benchmark_landscape", "run_slot_filling"]


def __getattr__(name):
    modules = {"run_prior_art_check": "prior_art_check", "run_benchmark_landscape": "benchmark_landscape", "run_slot_filling": "slot_filling"}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module("." + modules[name], __name__), name)
    globals()[name] = value
    return value
