# tools/code_executor.py

"""
Configuration for code execution environment.
"""

# Define authorized imports for the CodeAgent's Python executor
# Only include modules that are actually installed in the environment
AUTHORIZED_IMPORTS = [
    "requests",
    "json",
    "os",
    "sys",
    "pathlib",
    "typing",
    "datetime",
    "re",
    "math",
    "collections",
    "itertools",
    "functools",
    # Add more as needed, but ensure they are installed
    "csv",
    "random",
    "shutil",
]