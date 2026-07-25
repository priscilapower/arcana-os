"""Schemas for the filesystem builtins — the file set and the directory set.

These are the single source of truth for these tools' schemas: they feed the
aggregated ``BUILTIN_DEFINITIONS`` (see :mod:`arcana.tools.builtins.definitions`)
that both the model-visible registry and the executing adapter consume, so the
schema a model is offered can never drift from the schema the adapter honours.

The descriptions state the jail and the trash outright. A model that knows paths
are workspace-relative and that a delete is recoverable writes better arguments
than one that has to discover both from error messages.

Each definition also declares ``path_args`` — the arguments that carry a
filesystem path. It is not documentation: it is what a path-scoping guardrail
iterates, so ``move`` and ``copy`` are checked on ``dst`` as well as ``src``
rather than being confined by their first argument alone.
"""

from arcana.types.tool import BuiltinTool, ToolDefinition, ToolType

#: The path argument the single-path filesystem tools name their target with.
PATH_ARG = "path"

#: The two path arguments ``move`` and ``copy`` take.
SRC_ARG = "src"
DST_ARG = "dst"

LIST_DIR = ToolDefinition(
    name=BuiltinTool.LIST_DIR,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "List the contents of one directory in the agent workspace. Returns each "
        "entry's name, kind (file, dir, symlink, other) and size. Not recursive. "
        "Defaults to the workspace root, so call it with no arguments to see what "
        "is there."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list. Defaults to the workspace root.",
            }
        },
    },
)

READ_FILE = ToolDefinition(
    name=BuiltinTool.READ_FILE,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "Read a UTF-8 text file from the agent workspace. Relative paths are "
        "resolved inside the workspace; paths outside it are refused. Long files "
        "are truncated."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to read."}},
        "required": ["path"],
    },
)

WRITE_FILE = ToolDefinition(
    name=BuiltinTool.WRITE_FILE,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "Write text to a file in the agent workspace. 'create' fails if the file "
        "exists, 'overwrite' replaces it, 'append' adds to the end. Relative paths "
        "are resolved inside the workspace; paths outside it are refused."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path to the file to write."},
            "content": {"type": "string", "description": "Text to write."},
            "mode": {
                "type": "string",
                "enum": ["create", "overwrite", "append"],
                "description": "How to treat an existing file. Defaults to 'create'.",
            },
        },
        "required": ["path", "content"],
    },
)

DELETE_FILE = ToolDefinition(
    name=BuiltinTool.DELETE_FILE,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "Delete a single file from the agent workspace. Directories are refused — "
        "use delete_dir for those. Relative paths are resolved inside the workspace. "
        "The file is moved to the workspace trash and stays recoverable unless hard "
        "deletion is enabled."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to delete."}},
        "required": ["path"],
    },
)

MAKE_DIR = ToolDefinition(
    name=BuiltinTool.MAKE_DIR,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "Create a directory in the agent workspace. Set 'parents' to also create any "
        "missing intermediate directories, and 'exist_ok' to succeed quietly when the "
        "directory is already there. Relative paths are resolved inside the workspace; "
        "paths outside it are refused."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Directory to create."},
            "parents": {
                "type": "boolean",
                "description": "Also create missing parent directories. Defaults to false.",
            },
            "exist_ok": {
                "type": "boolean",
                "description": "Succeed if the directory already exists. Defaults to false.",
            },
        },
        "required": ["path"],
    },
)

MOVE = ToolDefinition(
    name=BuiltinTool.MOVE,
    type=ToolType.BUILTIN,
    path_args=[SRC_ARG, DST_ARG],
    description=(
        "Move or rename a file or directory inside the agent workspace. Both paths are "
        "resolved inside the workspace and both must stay inside it. Fails if the "
        "destination already exists unless 'overwrite' is set, and refuses a destination "
        "inside the source."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Path to move from."},
            "dst": {"type": "string", "description": "Path to move to."},
            "overwrite": {
                "type": "boolean",
                "description": "Replace the destination if it exists. Defaults to false.",
            },
        },
        "required": ["src", "dst"],
    },
)

COPY = ToolDefinition(
    name=BuiltinTool.COPY,
    type=ToolType.BUILTIN,
    path_args=[SRC_ARG, DST_ARG],
    description=(
        "Copy a file or a whole directory tree inside the agent workspace. Both paths are "
        "resolved inside the workspace and both must stay inside it. Symlinks are copied "
        "as links and are skipped when they point outside the workspace. Fails if the "
        "destination already exists unless 'overwrite' is set, if the destination is "
        "inside the source, or if the tree is too large."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "Path to copy from."},
            "dst": {"type": "string", "description": "Path to copy to."},
            "overwrite": {
                "type": "boolean",
                "description": "Replace the destination if it exists. Defaults to false.",
            },
        },
        "required": ["src", "dst"],
    },
)

DELETE_DIR = ToolDefinition(
    name=BuiltinTool.DELETE_DIR,
    type=ToolType.BUILTIN,
    path_args=[PATH_ARG],
    description=(
        "Delete a directory and everything inside it from the agent workspace. Files are "
        "refused — use delete_file for those — and so is the workspace root itself. The "
        "whole tree is moved to the workspace trash and stays recoverable unless hard "
        "deletion is enabled."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory to delete, with its contents."}},
        "required": ["path"],
    },
)
