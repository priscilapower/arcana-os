"""Schemas for the filesystem builtins (``read_file`` / ``write_file`` / ``delete_file``).

These are the single source of truth for these three tools' schemas: they feed
the aggregated ``BUILTIN_DEFINITIONS`` (see :mod:`arcana.tools.builtins.definitions`)
that both the model-visible registry and the executing adapter consume, so the
schema a model is offered can never drift from the schema the adapter honours.

The descriptions state the jail and the trash outright. A model that knows paths
are workspace-relative and that a delete is recoverable writes better arguments
than one that has to discover both from error messages.
"""

from arcana.types.tool import BuiltinTool, ToolDefinition, ToolType

LIST_DIR = ToolDefinition(
    name=BuiltinTool.LIST_DIR,
    type=ToolType.BUILTIN,
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
    description=(
        "Delete a single file from the agent workspace. Directories are refused. "
        "Relative paths are resolved inside the workspace. The file is moved to the "
        "workspace trash and stays recoverable unless hard deletion is enabled."
    ),
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path to the file to delete."}},
        "required": ["path"],
    },
)
