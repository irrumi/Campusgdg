"""Run the student UI against the existing gateway; no GCP deployment.

The default mode uses the existing application access key entered in UI settings.
Only explicit --standalone requests personal Gemini/GitHub keys with hidden prompts.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def gateway_environment(source: dict[str, str], endpoint: str, *, direct: bool = False) -> dict[str, str]:
    endpoint = endpoint.strip()
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Укажите HTTPS-адрес существующего gateway без данных авторизации в URL.")
    environment = dict(source)
    for name in ("CAMPUS_LOCAL_ONLY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN"):
        environment.pop(name, None)
    environment.update(CAMPUS_SKIP_LOCAL_CONFIG="1", AGENT_API_URL=endpoint,
                       STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    if direct:
        environment["NO_PROXY"] = ",".join(filter(None, [source.get("NO_PROXY", ""), parsed.hostname, "127.0.0.1", "localhost"]))
        environment["no_proxy"] = environment["NO_PROXY"]
    return environment


def interface_command(port: int) -> list[str]:
    return [sys.executable, "-m", "streamlit", "run", "campus_chat_app.py",
            "--server.address", "127.0.0.1", "--server.port", str(port), "--server.headless", "true"]


def child_environments(source: dict[str, str], gemini_key: str, github_token: str,
                       gateway_key: str, *, gateway_port: int, model: str) -> tuple[dict, dict]:
    if not gemini_key.strip() or not github_token.strip() or len(gateway_key) < 16:
        raise ValueError("Нужны ключ Gemini API, GitHub token и локальный ключ gateway.")
    common = dict(source)
    for name in ("AGENT_ENGINE_RESOURCE", "VERTEX_AGENT_RESOURCE", "CAMPUS_API_KEY", "CAMPUS_API_KEY_SHA256",
                 "GEMINI_API_KEY", "GOOGLE_API_KEY", "GITHUB_TOKEN", "AGENT_API_KEY"):
        common.pop(name, None)
    common.update(CAMPUS_LOCAL_ONLY="1", CAMPUS_SKIP_LOCAL_CONFIG="1", CAMPUS_LLM_BACKEND="gemini",
                  GOOGLE_GENAI_USE_VERTEXAI="false", GOOGLE_GENAI_USE_ENTERPRISE="false",
                  GEMINI_MODEL=model, STREAMLIT_BROWSER_GATHER_USAGE_STATS="false")
    gateway = dict(common, GEMINI_API_KEY=gemini_key, GITHUB_TOKEN=github_token,
                   CAMPUS_API_KEY_SHA256=hashlib.sha256(gateway_key.encode()).hexdigest())
    interface = dict(common, AGENT_API_URL=f"http://127.0.0.1:{gateway_port}/api/v1/chat", AGENT_API_KEY=gateway_key)
    return gateway, interface


def check_port(port: int) -> None:
    if not 1024 <= port <= 65535:
        raise ValueError("Порт должен быть между 1024 и 65535.")
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            raise ValueError(f"Порт {port} занят. Выберите другой порт через параметры запуска.") from None


def stop_children(children):
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=8501)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"))
    parser.add_argument("--standalone", action="store_true", help="Run a local agent with personal Gemini/GitHub keys")
    parser.add_argument("--url", help="Existing HTTPS gateway; defaults to AGENT_API_URL or agent.connection.json")
    parser.add_argument("--direct", action="store_true", help="Bypass environment proxies for the selected gateway")
    args = parser.parse_args(argv)
    children = []
    try:
        check_port(args.ui_port)
        if not args.standalone:
            endpoint = args.url or os.environ.get("AGENT_API_URL")
            if not endpoint:
                endpoint = json.loads((ROOT / "agent.connection.json").read_text(encoding="utf-8"))["api_url"]
            environment = gateway_environment(dict(os.environ), endpoint, direct=args.direct)
            children.append(subprocess.Popen(interface_command(args.ui_port), cwd=ROOT, env=environment))
            print(f"Откройте http://127.0.0.1:{args.ui_port}. В настройках используйте прежний ключ gateway.", flush=True)
            print("Ключи Gemini/GitHub не требуются. Проверка — через gateway; планирование — локально.", flush=True)
            return children[0].wait()
        if args.gateway_port == args.ui_port:
            raise ValueError("Для gateway и интерфейса нужны разные порты.")
        check_port(args.gateway_port)
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or getpass.getpass("Ключ Gemini API (ввод скрыт): ")
        github_token = os.environ.get("GITHUB_TOKEN") or getpass.getpass("GitHub token для чтения репозитория (ввод скрыт): ")
        gateway_env, ui_env = child_environments(dict(os.environ), gemini_key.strip(), github_token.strip(),
            secrets.token_urlsafe(32), gateway_port=args.gateway_port, model=args.model)
        gateway = subprocess.Popen([sys.executable, "-m", "uvicorn", "agent.server:app", "--host", "127.0.0.1",
                                    "--port", str(args.gateway_port)], cwd=ROOT, env=gateway_env)
        children.append(gateway)
        endpoint = f"http://127.0.0.1:{args.gateway_port}/api/v1/chat"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if gateway.poll() is not None:
                raise RuntimeError("Локальный gateway не запустился. Проверьте зависимости.")
            try:
                with urlopen(endpoint, timeout=1) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError, OSError):
                time.sleep(0.2)
        else:
            raise RuntimeError("Локальный gateway не ответил за 30 секунд.")
        children.append(subprocess.Popen(interface_command(args.ui_port), cwd=ROOT, env=ui_env))
        print(f"\nОткройте http://127.0.0.1:{args.ui_port} и раздел «До сдачи».", flush=True)
        print("Локальный агент → Gemini API + GitHub MCP. Облачное развёртывание не используется.", flush=True)
        print("Ключи не записаны в файлы. Ctrl+C остановит оба локальных процесса.", flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        raise RuntimeError("Один из локальных процессов остановился. Остальные будут закрыты.")
    except KeyboardInterrupt:
        return 0
    except (EOFError, ValueError, RuntimeError) as exc:
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Ввод отменён.", file=sys.stderr)
        return 1
    finally:
        stop_children(children)


if __name__ == "__main__":
    raise SystemExit(main())
