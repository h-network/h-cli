"""h-cli Core — MCP server exposing tools over SSE via FastMCP."""

import subprocess

from mcp.server.fastmcp import FastMCP

from hbot_logging import get_logger, get_audit_logger

logger = get_logger(__name__, service="core")
audit = get_audit_logger("core")

mcp = FastMCP("h-cli-core")


@mcp.tool()
def run_command(command: str) -> str:
    """Execute a shell command in the core container (ParrotOS).

    Available tools: nmap, dig, traceroute, mtr, tcpdump, whois,
    curl, wget, ssh, ping, iproute2, netcat, Playwright/Chromium.
    Returns combined stdout+stderr and exit code.
    """
    logger.info("Executing command: %s", command)
    audit.info("command_exec", extra={"command": command})

    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=280,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Command timed out: %s", command)
        return "Error: command timed out after 280 seconds"

    output = ""
    if proc.stdout:
        output += proc.stdout
    if proc.stderr:
        if output:
            output += "\n"
        output += proc.stderr

    if not output:
        output = "(no output)"

    logger.info("Command finished (exit=%d): %s", proc.returncode, command)
    audit.info(
        "command_result",
        extra={
            "command": command,
            "exit_code": proc.returncode,
            "output_length": len(output),
        },
    )

    return f"Exit code: {proc.returncode}\n\n{output}"


if __name__ == "__main__":
    logger.info("Starting MCP server on 0.0.0.0:8083")
    mcp.run(transport="sse", host="0.0.0.0", port=8083)
