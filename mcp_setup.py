"""Connects the bot's MCP server to AI apps on whatever PC this runs on.

Paths are worked out at runtime (this folder, the Python running it), so moving the folder or
installing on another PC only needs one click on Connect again.

    python mcp_setup.py status
    python mcp_setup.py claude-desktop      # adds the server to Claude Desktop's config
    python mcp_setup.py claude-code         # adds it to Claude Code for every folder (user scope)
    python mcp_setup.py config              # prints the settings to paste into any other MCP app
"""

import glob
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_NAME = "linkedin-bot"
SERVER_FILE = HERE / "mcp_server.py"


def server_entry():
    # The full Python path, not "python": another PC may have several, or none on PATH.
    return {"command": sys.executable, "args": [str(SERVER_FILE)]}


def config_json():
    return json.dumps({"mcpServers": {SERVER_NAME: server_entry()}}, indent=2)


# ---------- Claude Desktop ----------

def claude_desktop_configs():
    """Every Claude Desktop config on this PC. The Windows Store app keeps its own copy."""
    home = Path.home()
    found = []
    if sys.platform == "win32":
        appdata, local = os.environ.get("APPDATA", ""), os.environ.get("LOCALAPPDATA", "")
        found += [Path(p) for p in glob.glob(os.path.join(local, "Claude-Data", "*", "claude_desktop_config.json"))]
        standard = Path(appdata) / "Claude" / "claude_desktop_config.json"
        if standard.exists() or standard.parent.exists():
            found.append(standard)
    elif sys.platform == "darwin":
        found.append(home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
    else:
        found.append(home / ".config" / "Claude" / "claude_desktop_config.json")
    return [p for p in dict.fromkeys(found) if p.exists() or p.parent.exists()]


def _read(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def connect_claude_desktop(log=print):
    paths = claude_desktop_configs()
    if not paths:
        log("Claude Desktop isn't installed on this PC (no config folder found).")
        return False
    for path in paths:
        data = _read(path)
        if path.exists():
            shutil.copy2(path, path.with_suffix(".json.bak"))      # keep the old settings, just in case
        data.setdefault("mcpServers", {})[SERVER_NAME] = server_entry()
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log(f"Claude Desktop: added '{SERVER_NAME}' to {path}")
    log("Quit Claude Desktop fully (tray icon > Quit) and open it again to load the bot's tools.")
    return True


def claude_desktop_status():
    paths = claude_desktop_configs()
    if not paths:
        return "not installed"
    entries = [_read(p).get("mcpServers", {}).get(SERVER_NAME) for p in paths]
    if any(e == server_entry() for e in entries):
        return "connected"
    return "needs update" if any(entries) else "not connected"


# ---------- Claude Code ----------

def claude_cli():
    return shutil.which("claude")


def connect_claude_code(log=print):
    cli = claude_cli()
    if not cli:
        log("Claude Code isn't installed on this PC (no 'claude' command found).")
        return False
    # Replace any old entry so a moved folder gets the new path. User scope works in every folder.
    subprocess.run([cli, "mcp", "remove", "--scope", "user", SERVER_NAME], capture_output=True, text=True)
    r = subprocess.run([cli, "mcp", "add", "--scope", "user", SERVER_NAME, "--", sys.executable, str(SERVER_FILE)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log(f"Claude Code: couldn't add it ({(r.stderr or r.stdout).strip()[:200]})")
        return False
    log(f"Claude Code: added '{SERVER_NAME}' for all folders. Start a new Claude Code session to use it.")
    return True


def claude_code_status():
    if not claude_cli():
        return "not installed"
    entry = _read(Path.home() / ".claude.json").get("mcpServers", {}).get(SERVER_NAME)
    if not entry:
        return "not connected"
    same = entry.get("command") == sys.executable and entry.get("args") == [str(SERVER_FILE)]
    return "connected" if same else "needs update"


def status():
    return {"claude_desktop": claude_desktop_status(), "claude_code": claude_code_status(), "config": config_json()}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "claude-desktop":
        connect_claude_desktop()
    elif cmd == "claude-code":
        connect_claude_code()
    elif cmd == "config":
        print(config_json())
    else:
        s = status()
        print(f"Claude Desktop: {s['claude_desktop']}\nClaude Code:    {s['claude_code']}")


if __name__ == "__main__":
    main()
