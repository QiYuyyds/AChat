"""ID generation utilities matching TypeScript implementation."""

from nanoid import generate

# Alphabet matching nanoid default
ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _gen_id(prefix: str, size: int = 12) -> str:
    """Generate a prefixed nanoid."""
    return f"{prefix}{generate(ALPHABET, size)}"


def new_agent_id() -> str:
    """Generate agent ID: ag_<nanoid>"""
    return _gen_id("ag_")


def new_conversation_id() -> str:
    """Generate conversation ID: conv_<nanoid>"""
    return _gen_id("conv_")


def new_message_id() -> str:
    """Generate message ID: msg_<nanoid>"""
    return _gen_id("msg_")


def new_error_message_id() -> str:
    """Generate error message ID: msg_err_<nanoid>"""
    return _gen_id("msg_err_")


def new_artifact_id() -> str:
    """Generate artifact ID: art_<nanoid>"""
    return _gen_id("art_")


def new_workspace_id() -> str:
    """Generate workspace ID: ws_<nanoid>"""
    return _gen_id("ws_")


def new_run_id() -> str:
    """Generate run ID: run_<nanoid>"""
    return _gen_id("run_")


def new_tool_call_id() -> str:
    """Generate tool call ID: call_<nanoid>"""
    return _gen_id("call_")


def new_attachment_id() -> str:
    """Generate attachment ID: att_<nanoid>"""
    return _gen_id("att_")


def new_pending_write_id() -> str:
    """Generate pending write ID: pwr_<nanoid>"""
    return _gen_id("pwr_")


def new_pending_question_id() -> str:
    """Generate pending question ID: pq_<nanoid>"""
    return _gen_id("pq_")


def new_pending_dispatch_plan_id() -> str:
    """Generate pending dispatch plan ID: pdp_<nanoid>"""
    return _gen_id("pdp_")


def new_pending_bash_command_id() -> str:
    """Generate pending bash command ID: pbc_<nanoid>"""
    return _gen_id("pbc_")


def new_context_summary_id() -> str:
    """Generate context summary ID: cs_<nanoid>"""
    return _gen_id("cs_")


def new_deployment_id() -> str:
    """Generate deployment ID: dep_<nanoid>"""
    return _gen_id("dep_")
