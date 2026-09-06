"""Aql Agent — Flask backend.

Frontend (templates/index.html) shu backend orqali ishlaydi:
- Sozlamalar (provayder, base URL, API kalit, model) `data/settings.json`da
- Suhbat tarixi `data/history.json`da JSON sifatida saqlanadi

Backend AI provayderga so'rovni server tomonidan yuboradi (browser
to'g'ridan-to'g'ri provayderga ulanmaydi), shuning uchun CORS
muammolari bo'lmaydi va API kalit faqat serverda saqlanadi.

Ishga tushirish:
    pip install -r requirements.txt
    python app.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.json"
SANDBOX_DIR = BASE_DIR / "sandbox"

DATA_DIR.mkdir(exist_ok=True)
SANDBOX_DIR.mkdir(exist_ok=True)
_lock = threading.Lock()

RUN_TIMEOUT = 15       # kod bloklarini ishga tushirish uchun (soniya)
TERMINAL_TIMEOUT = 30  # terminal buyruqlari uchun (soniya)
INSTALL_TIMEOUT = 180  # kutubxona o'rnatish uchun (soniya)

# Terminalning joriy ishchi papkasi (shu jarayon davomida saqlanadi).
TERMINAL_STATE = {"cwd": str(SANDBOX_DIR)}

PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]=<>!,~+]+$")

DEFAULT_SETTINGS = {
    "provider": "openrouter",
    "base_url": "https://openrouter.ai/api/v1",
    "api_key": "",
    "model": "",
    "system_prompt": "",
}

DEFAULT_SYSTEM_PROMPT = """You are Aql Agent, a capable general-purpose AI assistant for Uzbek-speaking users.

Core behavior:
- Answer in the user's language. Default to clear, natural Uzbek when language is ambiguous.
- Solve coding, writing, planning, analysis, learning, and creative tasks with practical detail.
- For code, provide complete and secure examples, explain important decisions, and use modern conventions.
- For complex requests, structure the response into concise steps and state assumptions.
- Never claim to have completed real-world actions you cannot perform.
- Protect private information and refuse harmful requests while suggesting a safe alternative.
- Be direct, thoughtful, and useful. Avoid unnecessary filler.
"""

app = Flask(__name__)


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return json.loads(json.dumps(default))
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return json.loads(json.dumps(default))


def write_json(path: Path, data: dict) -> None:
    tmp_path = path.with_suffix(".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def load_settings() -> dict:
    with _lock:
        return read_json(SETTINGS_FILE, DEFAULT_SETTINGS)


def save_settings(data: dict) -> dict:
    merged = {**DEFAULT_SETTINGS, **{k: v for k, v in data.items() if k in DEFAULT_SETTINGS}}
    with _lock:
        write_json(SETTINGS_FILE, merged)
    return merged


def load_history() -> dict:
    with _lock:
        return read_json(HISTORY_FILE, {"messages": []})


def save_history(data: dict) -> None:
    with _lock:
        write_json(HISTORY_FILE, data)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(load_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings():
    body = request.get_json(force=True, silent=True) or {}
    saved = save_settings(body)
    return jsonify(saved)


@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(load_history())


@app.route("/api/history/clear", methods=["POST"])
def clear_history():
    save_history({"messages": []})
    return jsonify({"ok": True})


def stream_chat_response(user_message: str):
    settings = load_settings()
    base_url = (settings.get("base_url") or "").strip()
    model = (settings.get("model") or "").strip()

    if not base_url or not model:
        yield f"data: {json.dumps({'error': 'Avval sozlamalarda base URL va modelni kiriting.'})}\n\n"
        return

    history = load_history()
    history["messages"].append({"role": "user", "content": user_message})
    save_history(history)

    system_prompt = settings.get("system_prompt") or DEFAULT_SYSTEM_PROMPT
    payload_messages = [{"role": "system", "content": system_prompt}]
    payload_messages += [{"role": m["role"], "content": m["content"]} for m in history["messages"]]

    headers = {"Content-Type": "application/json"}
    if settings.get("api_key"):
        headers["Authorization"] = f"Bearer {settings['api_key']}"

    url = base_url.rstrip("/") + "/chat/completions"
    full_text = ""

    try:
        resp = requests.post(
            url,
            headers=headers,
            json={"model": model, "messages": payload_messages, "stream": True},
            stream=True,
            timeout=120,
        )
    except requests.RequestException as exc:
        yield f"data: {json.dumps({'error': f'Ulanish xatosi: {exc}'})}\n\n"
        return

    if resp.status_code != 200:
        err_text = resp.text[:300]
        yield f"data: {json.dumps({'error': f'HTTP {resp.status_code}: {err_text}'})}\n\n"
        return

    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
                delta = obj.get("choices", [{}])[0].get("delta", {}).get("content", "")
            except (json.JSONDecodeError, IndexError, KeyError):
                continue
            if delta:
                full_text += delta
                yield f"data: {json.dumps({'delta': delta})}\n\n"
    except requests.RequestException as exc:
        yield f"data: {json.dumps({'error': f'Oqim uzildi: {exc}'})}\n\n"

    if full_text:
        history = load_history()
        history["messages"].append({"role": "assistant", "content": full_text})
        save_history(history)

    yield f"data: {json.dumps({'done': True})}\n\n"


@app.route("/api/chat", methods=["POST"])
def chat():
    body = request.get_json(force=True, silent=True) or {}
    message = (body.get("message") or "").strip()
    if not message:
        return jsonify({"error": "Xabar bo'sh bo'lishi mumkin emas."}), 400

    return Response(stream_with_context(stream_chat_response(message)), mimetype="text/event-stream")


# ---------------------------------------------------------------------------
# Kod ishga tushirish, kutubxona o'rnatish va terminal
#
# DIQQAT: bu endpointlar serverda haqiqiy buyruq/kod bajaradi. Ular faqat
# shaxsiy, ishonchli muhitda (o'z kompyuteringizda) ishlatish uchun mo'ljallangan.
# Bu ilovani hech qachon ochiq internetga (masalan 0.0.0.0 bilan tashqi
# tarmoqqa) joylashtirmang — aks holda istalgan kishi serveringizda
# buyruq bajarishi mumkin bo'lib qoladi.
# ---------------------------------------------------------------------------


def sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def stream_subprocess(cmd, cwd: str, timeout: int, shell: bool = False):
    """Jarayonni ishga tushiradi va stdout/stderr'ni real vaqtda (satrma-satr)
    hosil qiladi. Har bir hodisa {'type': 'stdout'|'stderr', 'data': str}
    ko'rinishida, oxirida {'type': 'exit', 'code': int} yoki
    {'type': 'error', 'message': str} qaytariladi."""
    import queue
    import time

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            shell=shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        yield {"type": "error", "message": f"Dastur topilmadi: {exc}"}
        return
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": str(exc)}
        return

    q: "queue.Queue" = queue.Queue()

    def reader(stream, tag):
        try:
            for line in iter(stream.readline, ""):
                q.put((tag, line))
        finally:
            stream.close()
            q.put((f"{tag}_done", None))

    t_out = threading.Thread(target=reader, args=(proc.stdout, "stdout"), daemon=True)
    t_err = threading.Thread(target=reader, args=(proc.stderr, "stderr"), daemon=True)
    t_out.start()
    t_err.start()

    start = time.time()
    finished = {"stdout": False, "stderr": False}
    timed_out = False

    while True:
        try:
            tag, data = q.get(timeout=0.4)
            if tag.endswith("_done"):
                finished[tag[: -len("_done")]] = True
                if all(finished.values()):
                    break
                continue
            yield {"type": tag, "data": data}
        except queue.Empty:
            pass

        if time.time() - start > timeout:
            timed_out = True
            try:
                proc.kill()
            except OSError:
                pass
            break

    if timed_out:
        yield {"type": "error", "message": f"Vaqt tugadi ({timeout}s ichida yakunlanmadi), jarayon to'xtatildi."}
        try:
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
        yield {"type": "exit", "code": -1}
        return

    proc.wait()
    yield {"type": "exit", "code": proc.returncode}


@app.route("/api/run", methods=["POST"])
def run_code():
    body = request.get_json(force=True, silent=True) or {}
    language = (body.get("language") or "").lower().strip()
    code = body.get("code") or ""

    if not code.strip():
        return jsonify({"error": "Kod bo'sh."}), 400

    if language == "html":
        # HTML server tomonida bajarilmaydi — brauzerda sandboxed iframe'da ko'rsatiladi.
        return jsonify({"html": code})

    file_id = uuid.uuid4().hex[:8]
    path = None
    cmd = None

    if language in ("python", "py"):
        path = SANDBOX_DIR / f"run_{file_id}.py"
        path.write_text(code, encoding="utf-8")
        cmd = [sys.executable, "-u", str(path)]
    elif language in ("javascript", "js", "node"):
        if shutil.which("node") is None:
            return jsonify({"error": "Serverda Node.js topilmadi. O'rnatilgach qaytadan urinib ko'ring."}), 400
        path = SANDBOX_DIR / f"run_{file_id}.js"
        path.write_text(code, encoding="utf-8")
        cmd = ["node", str(path)]
    elif language in ("bash", "sh", "shell"):
        path = SANDBOX_DIR / f"run_{file_id}.sh"
        path.write_text(code, encoding="utf-8")
        cmd = ["bash", str(path)]
    else:
        return jsonify({"error": f"'{language}' tili qo'llab-quvvatlanmaydi (python, javascript, bash, html)."}), 400

    def generate():
        try:
            for event in stream_subprocess(cmd, cwd=str(SANDBOX_DIR), timeout=RUN_TIMEOUT):
                yield sse(event)
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/install", methods=["POST"])
def install_package():
    body = request.get_json(force=True, silent=True) or {}
    raw = (body.get("package") or "").strip()
    if not raw:
        return jsonify({"error": "Kutubxona nomi bo'sh."}), 400

    packages = raw.split()
    if not all(PACKAGE_NAME_RE.match(p) for p in packages):
        return jsonify({"error": "Kutubxona nomida ruxsat etilmagan belgilar bor."}), 400

    cmd = [sys.executable, "-u", "-m", "pip", "install", "--break-system-packages", *packages]

    def generate():
        for event in stream_subprocess(cmd, cwd=str(SANDBOX_DIR), timeout=INSTALL_TIMEOUT):
            yield sse(event)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/terminal", methods=["GET"])
def terminal_state():
    return jsonify({"cwd": TERMINAL_STATE["cwd"]})


@app.route("/api/terminal", methods=["POST"])
def terminal():
    body = request.get_json(force=True, silent=True) or {}
    command = (body.get("command") or "").strip()
    if not command:
        return jsonify({"error": "Buyruq bo'sh."}), 400

    cwd = TERMINAL_STATE["cwd"]

    def generate():
        if command == "cd" or command.startswith("cd "):
            target = command[2:].strip() or str(SANDBOX_DIR)
            candidate = Path(target) if target.startswith("/") else Path(cwd) / target
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if resolved.exists() and resolved.is_dir():
                TERMINAL_STATE["cwd"] = str(resolved)
                yield sse({"type": "cwd", "cwd": TERMINAL_STATE["cwd"]})
                yield sse({"type": "exit", "code": 0})
            else:
                yield sse({"type": "stderr", "data": f"Papka topilmadi: {target}\n"})
                yield sse({"type": "cwd", "cwd": cwd})
                yield sse({"type": "exit", "code": 1})
            return

        for event in stream_subprocess(command, cwd=cwd, timeout=TERMINAL_TIMEOUT, shell=True):
            yield sse(event)
        yield sse({"type": "cwd", "cwd": TERMINAL_STATE["cwd"]})

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
