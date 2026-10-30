"""Bridge between the Electron window and the bot.

Electron starts this and talks to it with one JSON object per line:
  in  (stdin):  {"cmd": "start", "kind": "jobs", "query": "...", "location": "", "pages": 3,
                 "speed": "medium", "grab_links": false}      (kind "boards" = company career sites)
                {"cmd": "stop"}  {"cmd": "chrome_status"}  {"cmd": "open_chrome"}
                {"cmd": "boards_info"}  {"cmd": "boards_add", "text": "Stripe"}  {"cmd": "boards_import"}
  out (stdout): {"type": "log" | "status" | "row" | "done" | "chrome" | "columns" | "boards", ...}
"""

import json
import subprocess
import sys
import threading

import job_boards
import linkedin_bot as bot

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_out_lock = threading.Lock()
stop = threading.Event()
worker = None


def send(type_, **data):
    with _out_lock:
        sys.stdout.write(json.dumps({"type": type_, **data}) + "\n")
        sys.stdout.flush()


def report(kind, value):
    # The table only shows short columns; full descriptions (up to 32k chars) stay in the Excel file.
    if kind == "row":
        value = [v[:300] if isinstance(v, str) else v for v in value]
    send(kind, value=value)


def start(msg):
    global worker
    if worker and worker.is_alive():
        send("log", text="Already running.")
        return
    kind = msg["kind"]
    stop.clear()
    if kind == "boards":
        send("columns", kind=kind, columns=job_boards.COLUMNS, link=job_boards.LINK_COLUMN)
    else:
        send("columns", kind=kind, columns=bot.COLUMNS[kind], link=bot.LINK_COLUMN[kind])

    def work():
        try:
            if kind == "boards":
                path = job_boards.run(msg["query"], msg.get("location", ""), log=lambda t: send("log", text=t),
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
        send("done", path=str(path) if path else None)

    worker = threading.Thread(target=work, daemon=True)
    worker.start()


def open_chrome():
    if bot.chrome_running():
        send("log", text="Chrome is already open. Make sure you're logged into LinkedIn there.")
        return
    subprocess.Popen([CHROME, "--remote-debugging-port=9222",
                      f"--user-data-dir={bot.Path(__file__).parent / 'chrome-profile'}",
                      "https://www.linkedin.com/"])
    send("log", text="Opened Chrome. Log into LinkedIn there (only needed the first time).")


def boards_info():
    companies = job_boards.load_companies()
    counts = {}
    for platform, _, _ in companies:
        counts[job_boards.PLATFORMS[platform]] = counts.get(job_boards.PLATFORMS[platform], 0) + 1
    send("boards", count=len(companies), by_platform=counts, file=str(job_boards.COMPANIES_FILE))


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
        elif cmd == "boards_import":
            # With "file": only that LinkedIn jobs search (the button after a run). Without: every jobs file.
            path = msg.get("file")
            files = [path] if path and bot.Path(path).name.startswith("jobs_") else None
            boards_task(lambda: job_boards.import_from_results(log=lambda t: send("log", text=t), files=files))


if __name__ == "__main__":
    main()
