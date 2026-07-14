import shlex
import subprocess

from .config import settings


FORBIDDEN_CHARS = [";", "&&", "||", "|", "`", "$(", ">", "<", "\n"]


class UnsafeCommandError(Exception):
    pass


def validate(command: str) -> list[str]:
    for bad in FORBIDDEN_CHARS:
        if bad in command:
            raise UnsafeCommandError(
                f"Command contains forbidden shell metacharacter '{bad}': {command}"
            )

    tokens = shlex.split(command)
    if not tokens:
        raise UnsafeCommandError("Empty command")

    executable = tokens[0]
    if executable not in settings.allowed_commands:
        raise UnsafeCommandError(
            f"Executable '{executable}' is not in the allowlist ({settings.allowed_commands})"
        )

    return tokens


def execute(command: str, timeout: int = 30) -> dict:
    """Run an allowlisted command and return {success, exit_code, output}."""
    try:
        tokens = validate(command)
    except UnsafeCommandError as e:
        return {"success": False, "exit_code": -1, "output": f"[REJECTED] {e}"}

    try:
        result = subprocess.run(
            tokens,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        combined = (result.stdout or "") + (result.stderr or "")
        return {
            "success": result.returncode == 0,
            "exit_code": result.returncode,
            "output": combined.strip() or "(no output)",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "exit_code": -2, "output": f"[TIMEOUT] after {timeout}s"}
    except FileNotFoundError:
        return {"success": False, "exit_code": -3, "output": f"[NOT FOUND] {tokens[0]}"}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "exit_code": -4, "output": f"[ERROR] {e}"}
