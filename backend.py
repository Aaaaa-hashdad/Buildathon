"""Bridge between the Electron window and the bot.

Electron starts this and talks to it with one JSON object per line:
  in  (stdin):  {"cmd": "start", "kind": "jobs", "query": "...", "location": "", "pages": 3,
                 "speed": "medium", "grab_links": false}      (kind "boards" = company career sites,
                                                                    "public" = open job feeds + career sites)
                {"cmd": "stop"}  {"cmd": "chrome_status"}  {"cmd": "open_chrome"}
                {"cmd": "boards_info"}  {"cmd": "boards_add", "text": "Stripe"}  {"cmd": "boards_import"}
  out (stdout): {"type": "log" | "status" | "row" | "done" | "chrome" | "columns" | "boards", ...}
"""

import json
import os
import random
import subprocess
import sys
import threading
import time

import job_boards
import linkedin_bot as bot
import mcp_setup
import open_sources
import superhuman_docs

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_out_lock = threading.Lock()
stop = threading.Event()
worker = None


APP_LOG = bot.Path(__file__).parent / "app_log.txt"      # everything the window's log shows; git-ignored


def send(type_, **data):
    if type_ == "log":
        try:
            with APP_LOG.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {data.get('text', '')}\n")
        except OSError:
            pass
    with _out_lock:
        sys.stdout.write(json.dumps({"type": type_, **data}) + "\n")
        sys.stdout.flush()


def report(kind, value):
    # The table only shows short columns; full descriptions (up to 32k chars) stay in the Excel file.
    if kind == "row":
        value = [v[:300] if isinstance(v, str) else v for v in value]
    send(kind, value=value)


def set_search_status(state, **extra):
    try:
        bot.SEARCH_STATUS_FILE.write_text(json.dumps({"state": state, "time": time.time(), **extra}), encoding="utf-8")
    except OSError:
        pass


def start(msg):
    global worker
    if worker and worker.is_alive():
        send("log", text="Already running.")
        return
    kind = msg["kind"]
    stop.clear()
    if kind in ("boards", "public"):
        send("columns", kind=kind, columns=job_boards.COLUMNS, link=job_boards.LINK_COLUMN)
    else:
        send("columns", kind=kind, columns=bot.COLUMNS[kind], link=bot.LINK_COLUMN[kind])

    def work():
        if not (ai_runner and ai_runner.is_alive()):
            set_search_status("running", query=msg.get("query", ""), kind=kind)
        try:
            if kind not in ("boards", "public") and msg.get("hidden") and not bot.chrome_running():
                # Same Chrome, same login, just parked off screen. The first login needs the visible one.
                send("log", text="Starting Chrome in the background (no window)...")
                bot.launch_chrome(hidden=True)
                if not bot.wait_for_chrome():
                    send("log", text="Chrome didn't start. Try 'Open Chrome' and log into LinkedIn first.")
                    send("done", path=None)
                    return
            if kind in ("boards", "public"):
                run = open_sources.run if kind == "public" else job_boards.run
                path = run(msg["query"], msg.get("location", ""), log=lambda t: send("log", text=t),
                           stop=stop, report=report)
            else:
                path = bot.run(kind, msg["query"], msg.get("location", ""),
                               max(1, min(int(msg.get("pages", 3)), bot.MAX_PAGES)),
                               log=lambda t: send("log", text=t), stop=stop,
                               report=report, speed=msg.get("speed", "medium"),
                               grab_apply_links=bool(msg.get("grab_links")))
        except Exception as e:
            send("log", text=f"Error: {e}")
            path = None
        name = bot.Path(path).name if path else None
        if ai_runner and ai_runner.is_alive():
            if name:
                batch_files.append(name)
        else:
            set_search_status("done", file=name, files=[name] if name else [], query=msg.get("query", ""))
        send("done", path=str(path) if path else None)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()


last_ai_search = 0.0
ai_runner = None
batch_files = []     # result files made by the AI batch that is running
AI_GAP = (40, 70)       # seconds between one title's search and the next, like a person pausing


def run_ai_searches(searches):
    # An AI can only leave a note; the app opens Chrome and runs each search itself, one at a time.
    global last_ai_search
    batch_files.clear()
    try:
        if not bot.chrome_running():
            if not bot.launch_chrome() or not bot.wait_for_chrome():
                send("log", text="Chrome didn't open. Click Open Chrome, log into LinkedIn, then ask again.")
                return
            send("log", text="Opened Chrome. If LinkedIn asks you to log in, do it there and ask the AI again.")
        for i, s in enumerate(searches, 1):
            set_search_status("running", step=f"{i}/{len(searches)}", query=s["query"], files=list(batch_files))
            send("log", text=f"AI search {i}/{len(searches)}: '{s['query']}'")
            before = worker
            send("ai_search", **s)
            for _ in range(60):         # the window presses Start for us
                if worker is not before:
                    break
                time.sleep(0.5)
            else:
                send("log", text="The window didn't start the search. Stopping the AI searches.")
                return
            worker.join()
            if bot.problem:
                send("log", text="LinkedIn needs attention (see above), so the remaining AI searches are cancelled.")
                break
            if i < len(searches):
                gap = random.randint(*AI_GAP)
                send("log", text=f"Waiting {gap}s before the next title, like a person would.")
                time.sleep(gap)
    finally:
        last_ai_search = time.time()
        set_search_status("done", files=list(batch_files), file=batch_files[-1] if batch_files else None,
                          problem=bot.problem)


def handle_search_request():
    global ai_runner
    try:
        req = json.loads(bot.SEARCH_REQUEST_FILE.read_text(encoding="utf-8"))
        bot.SEARCH_REQUEST_FILE.unlink()
    except (OSError, ValueError):
        return
    searches = [{"query": str(q.get("query", "")).strip()[:60], "location": str(q.get("location", ""))[:60],
                 "pages": max(1, min(int(q.get("pages", 2)), bot.AI_MAX_PAGES))}
                for q in req.get("searches", [])[:bot.AI_MAX_TITLES] if str(q.get("query", "")).strip()]
    if not searches or time.time() - req.get("time", 0) > 120:
        return
    if (worker and worker.is_alive()) or (ai_runner and ai_runner.is_alive()) or time.time() - last_ai_search < 60:
        send("log", text="The AI asked for LinkedIn searches, but one is running or just ran. Ignored.")
        if not ((worker and worker.is_alive()) or (ai_runner and ai_runner.is_alive())):
            set_search_status("idle", note="ignored: a search just ran")
        return
    ai_runner = threading.Thread(target=run_ai_searches, args=(searches,), daemon=True)
    ai_runner.start()


def watch_plan():
    # Go (or any AI) writes search_plan.json with set_search_plan; the window shows it as soon as it changes.
    # Also the app's heartbeat, and where search requests from the AI are picked up.
    last = None
    while True:
        try:
            bot.APP_ALIVE_FILE.write_text(str(time.time()), encoding="utf-8")
            if bot.SEARCH_REQUEST_FILE.exists():
                threading.Thread(target=handle_search_request, daemon=True).start()
        except OSError:
            pass
        try:
            stamp = bot.PLAN_FILE.stat().st_mtime
        except OSError:
            stamp = None
        if stamp != last:
            last = stamp
            if stamp:
                try:
                    send("plan", **json.loads(bot.PLAN_FILE.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
        time.sleep(2)


def open_chrome():
    if not bot.launch_chrome():
        send("log", text="Chrome is already open. Make sure you're logged into LinkedIn there.")
        return
    send("log", text="Opened Chrome. Log into LinkedIn there (only needed the first time).")


def boards_info():
    companies = job_boards.load_companies()
    counts = {}
    for platform, _, _ in companies:
        counts[job_boards.PLATFORMS[platform]] = counts.get(job_boards.PLATFORMS[platform], 0) + 1
    send("boards", count=len(companies), by_platform=counts, file=str(job_boards.COMPANIES_FILE))


web_thread = None
tunnel_proc = None
tunnel_base = ""        # https://something.trycloudflare.com once the tunnel is up


def web_status():
    import mcp_server
    running = bool(web_thread and web_thread.is_alive())
    url = ""
    if running:
        path = mcp_server.web_url().split("/mcp/", 1)[1]
        url = f"{tunnel_base}/mcp/{path}" if tunnel_base else mcp_server.web_url()
    send("mcp_web", running=running, url=url, public=bool(tunnel_base))


def find_cloudflared():
    import shutil
    for p in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe"):
        if p and os.path.exists(p):
            return p
    return None


def find_ngrok():
    import shutil
    import glob
    winget = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet")
    # winget puts it in Links or, for the zip package, in Packages\Ngrok.Ngrok_*; a running app has an old PATH
    for p in (shutil.which("ngrok"), os.path.join(winget, "Links", "ngrok.exe"),
              *glob.glob(os.path.join(winget, "Packages", "Ngrok.Ngrok_*", "ngrok.exe"))):
        if p and os.path.exists(p):
            return p
    return None


def start_ngrok(port, domain):
    # A reserved ngrok domain never changes, so the connector only has to be set up once.
    global tunnel_proc, tunnel_base
    import atexit
    import time
    exe = find_ngrok()
    if not exe:
        send("log", text="ngrok isn't installed. Run 'winget install ngrok.ngrok', then "
                         "'ngrok config add-authtoken <your token>', and click Start web mode again.")
        return False
    tunnel_proc = subprocess.Popen(
        [exe, "http", str(port), f"--url=https://{domain}", f"--host-header=localhost:{port}", "--log=stdout"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    atexit.register(tunnel_proc.terminate)
    errors = []

    def watch():
        global tunnel_base
        for line in tunnel_proc.stdout:     # keep reading so the pipe never fills
            if "started tunnel" in line and not tunnel_base:
                tunnel_base = f"https://{domain}"
                send("log", text=f"Public URL ready (it stays the same every time): {tunnel_base}")
                web_status()
            elif "lvl=eror" in line or "ERR_NGROK" in line:
                errors.append(line.strip())
        tunnel_base = ""
        send("log", text="ngrok closed. " + (errors[-1][:300] if errors else "Click Start web mode to retry."))
        web_status()
    threading.Thread(target=watch, daemon=True).start()
    return True


def kill_old_tunnels(port):
    # An app that was killed (not closed) leaves its tunnel running; stop only tunnels made for this port.
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
             f"Where-Object {{ $_.CommandLine -like '*127.0.0.1:{port}*' }} | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            timeout=15, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        pass


TUNNEL_STATE = bot.Path(__file__).parent / "tunnel_state.json"     # pid + address of the tunnel; git-ignored
TUNNEL_LOG = bot.Path(__file__).parent / "tunnel.log"


def cloudflared_alive(pid):
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                             text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "cloudflared" in out.lower()


def start_tunnel(port):
    # claude.ai and Superhuman call the URL from their own servers, so localhost can't work. A free Cloudflare
    # quick tunnel gives this PC a public https address. The tunnel runs on its own (not as part of this
    # window), so closing or restarting the app keeps the same address; the connector is added once.
    # It only changes if the tunnel stops (PC restart, or closing cloudflared). A "domain" in mcp_web.json
    # (ngrok) still wins when set.
    global tunnel_proc, tunnel_base
    import re
    import mcp_server
    domain = mcp_server.web_settings().get("domain", "").replace("https://", "").strip("/ ")
    if domain:
        return start_ngrok(port, domain)
    exe = find_cloudflared()
    if not exe:
        send("log", text="cloudflared isn't installed, so there's no public URL. Install it with "
                         "'winget install Cloudflare.cloudflared' and click Start web mode again.")
        return

    def follow(pid):
        # Tells the window if the tunnel ever stops, so a dead address isn't shown as working.
        global tunnel_base
        while cloudflared_alive(pid):
            time.sleep(10)
        tunnel_base = ""
        send("log", text="The tunnel stopped. Click Start web mode to get a new URL (you'll re-add the connector).")
        web_status()

    try:
        state = json.loads(TUNNEL_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("port") == port and state.get("url") and cloudflared_alive(state.get("pid", 0)):
        tunnel_base = state["url"]
        send("log", text="Reusing the running tunnel: the URL is the same as before, no need to re-add the connector.")
        web_status()
        threading.Thread(target=follow, args=(state["pid"],), daemon=True).start()
        return

    kill_old_tunnels(port)
    with TUNNEL_LOG.open("w", encoding="utf-8") as log_file:
        tunnel_proc = subprocess.Popen(
            [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
             "--http-host-header", f"localhost:{port}"],
            stdout=subprocess.DEVNULL, stderr=log_file,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)

    def watch():
        global tunnel_base
        for _ in range(120):        # the URL is printed in the log file within ~10 seconds
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", TUNNEL_LOG.read_text(encoding="utf-8", errors="replace"))
            if m:
                tunnel_base = m.group(0)
                TUNNEL_STATE.write_text(json.dumps({"pid": tunnel_proc.pid, "port": port, "url": tunnel_base}),
                                        encoding="utf-8")
                send("log", text="Public URL ready. Paste it into the connector's URL field; it stays the same "
                                 "when you restart the app.")
                web_status()
                return follow(tunnel_proc.pid)
            if tunnel_proc.poll() is not None:
                break
            time.sleep(0.5)
        send("log", text="The tunnel didn't start. Click Start web mode to try again.")
        web_status()
    threading.Thread(target=watch, daemon=True).start()


web_lock = threading.Lock()


def start_web():
    with web_lock:      # the window and the backend may both ask at launch
        _start_web()


def _start_web():
    # Web-mode MCP (for Superhuman Go) runs inside this process, so it stops when the app closes.
    global web_thread
    import socket
    import mcp_server
    if web_thread and web_thread.is_alive():
        return web_status()
    cfg = mcp_server.web_settings()
    port = cfg["port"]
    if not cfg.get("autostart"):        # remembered, so web mode comes back by itself after a restart
        mcp_server.WEB_CONFIG.write_text(json.dumps({**cfg, "autostart": True}, indent=1), encoding="utf-8")
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        send("log", text=f"Port {port} is already in use, so web mode can't start. Close whatever uses it, "
                         f"or change the port in mcp_web.json.")
        return web_status()
    except OSError:
        pass        # nothing there: free to start

    def serve():
        try:
            mcp_server.web_server().run("streamable-http")
        except Exception as e:
            send("log", text=f"Web mode stopped: {e}")
    web_thread = threading.Thread(target=serve, daemon=True)
    web_thread.start()
    import time
    time.sleep(1.5)     # let it bind before reporting the URL
    send("log", text="Web mode is on. Opening a public tunnel (about 10 seconds)...")
    web_status()
    if not (tunnel_proc and tunnel_proc.poll() is None):
        start_tunnel(port)


def superhuman_send(path):
    # Sets the doc up on first use, then adds or updates the file's jobs in the Applications table.
    def work():
        say = lambda t: send("log", text=t)
        try:
            if not superhuman_docs.status()["ready"] and not superhuman_docs.setup(log=say):
                return
            superhuman_docs.send(path, log=say)
        except superhuman_docs.NotConnected as e:
            say(str(e))
        except Exception as e:
            say(f"Superhuman Docs error: {e}")
        finally:
            send("superhuman", **superhuman_docs.status())
    threading.Thread(target=work, daemon=True).start()


def boards_task(fn):
    # Adding and importing hit the network, so they run off the main loop.
    def work():
        try:
            fn()
        except Exception as e:
            send("log", text=f"Error: {e}")
        boards_info()
    threading.Thread(target=work, daemon=True).start()


def main():
    send("ready", output_dir=str(bot.OUTPUT_DIR))
    try:
        import mcp_server
        if mcp_server.web_settings().get("autostart"):
            threading.Thread(target=start_web, daemon=True).start()
    except Exception as e:
        send("log", text=f"Couldn't restart web mode: {e}")
    threading.Thread(target=watch_plan, daemon=True).start()
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        cmd = msg.get("cmd")
        if cmd == "start":
            start(msg)
        elif cmd == "stop":
            stop.set()
        elif cmd == "chrome_status":
            send("chrome", connected=bot.chrome_running())
        elif cmd == "open_chrome":
            open_chrome()
        elif cmd == "boards_info":
            boards_info()
        elif cmd == "boards_add":
            text = msg.get("text", "").strip()
            if text:
                boards_task(lambda: job_boards.add(text, log=lambda t: send("log", text=t)))
        elif cmd == "mcp_status":
            send("mcp", **mcp_setup.status())
            web_status()
        elif cmd == "mcp_web_start":
            threading.Thread(target=start_web, daemon=True).start()
        elif cmd in ("mcp_connect_desktop", "mcp_connect_code"):
            def connect(fn=mcp_setup.connect_claude_desktop if cmd == "mcp_connect_desktop"
                        else mcp_setup.connect_claude_code):
                try:
                    fn(log=lambda t: send("log", text=t))
                except Exception as e:
                    send("log", text=f"Couldn't connect: {e}")
                send("mcp", **mcp_setup.status())
            threading.Thread(target=connect, daemon=True).start()     # the claude CLI can take a few seconds
        elif cmd == "superhuman_status":
            send("superhuman", **superhuman_docs.status())
        elif cmd == "superhuman_send" and msg.get("file"):
            superhuman_send(msg["file"])
        elif cmd == "boards_import":
            # With "file": only that LinkedIn jobs search (the button after a run). Without: every jobs file.
            path = msg.get("file")
            files = [path] if path and bot.Path(path).name.startswith("jobs_") else None
            boards_task(lambda: job_boards.import_from_results(log=lambda t: send("log", text=t), files=files))


if __name__ == "__main__":
    main()
