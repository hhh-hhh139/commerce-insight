"""Run the personal loopback demo. Default: zero external model calls."""
import argparse
from contextlib import nullcontext
import fcntl
import getpass
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from commerce_worker.worker import Client, child_environment
from scripts.java_local import environment, prepare, server_command
from scripts.state_guard import uses_state


def stop_group(process):
    if process is None:
        return
    # Worker executors inherit its dedicated process group. Do not leave them behind.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


@uses_state
def main():
    parser = argparse.ArgumentParser(description="Commerce Insight 本机网页演示")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--live", action="store_true", help="个人终端隐藏输入 Key，启用付费模型；共用已设置的 5 元账本")
    parser.add_argument("--owner",action="store_true",help="启用单店主登录；先运行 scripts/prepare_owner.py")
    parser.add_argument("--origin",help="owner 模式唯一外部 Origin；默认当前回环地址")
    parser.add_argument("--bind",choices=("127.0.0.1","0.0.0.0"),default="127.0.0.1")
    parser.add_argument("--runtime",type=Path,help="项目 .local 内独立运行目录；不读取机器既有凭据")
    parser.add_argument("--postgres", action="store_true", help="使用显式 COMMERCE_DB_URL/USER/PASSWORD，不读取已有数据库配置")
    parser.add_argument("--db-secrets", type=Path, help="PostgreSQL 专用 configtree 目录，含 COMMERCE_DB_PASSWORD 文件")
    parser.add_argument("--console-logs", action="store_true", help="子进程输出交给容器的有界日志驱动")
    args = parser.parse_args()
    if args.db_secrets and not args.postgres: parser.error("--db-secrets 需要 --postgres")
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if not args.owner and (args.origin or args.bind!="127.0.0.1"):parser.error("外部地址配置需要 --owner")
    origin=args.origin or ("http://127.0.0.1:"+str(args.port))
    if args.bind!="127.0.0.1" and not origin.startswith("https://"):parser.error("外部监听要求 HTTPS Origin 与 TLS 反向代理")
    if not (ROOT / "services/business-api/target/business-api-0.1.0.jar").is_file():
        parser.error("先运行 .local/agent-venv/bin/python scripts/java_local.py build")
    # Fail before starting anything; never stop or reuse an unknown service.
    try:
        with socket.socket() as probe:
            # Allow our recently closed listener's TIME_WAIT sockets, not an active listener.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((args.bind, args.port))
    except OSError:
        parser.error("端口已被占用；请选择 --port，不会操作占用该端口的进程")
    runtime = prepare(args.runtime or ROOT / (".local/owner-demo" if args.owner else ".local/web-demo") / ("live" if args.live else "offline"))
    if (runtime / "RESTORE_INCOMPLETE").exists() or (ROOT / ".local/single-agent/RESTORE_INCOMPLETE").exists():
        parser.error("恢复尚未完成，禁止启动")
    if args.owner and not (runtime/"owner.hash").is_file():parser.error("先用 scripts/prepare_owner.py --runtime 设置此模式运行目录中的应用密码")
    with (runtime / "launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("同一演示模式已有启动器运行")
        worker_env = child_environment()
        worker_env.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)  # Never inherit a key silently.
        if args.live:
            if not sys.stdin.isatty():
                parser.error("实时模式必须在你自己的交互终端启动，Key 不通过命令参数或文件")
            from commerce_agent.budget import Budget, CAP, PRICE_DATE, RESERVATION
            from commerce_agent.storage import AgentError
            try:
                ledger = Budget(ROOT / (".local/single-agent/personal-budget-" + PRICE_DATE + ".json")).read()
            except AgentError as exc:
                parser.error(str(exc))
            used = sum(c["charged_micro_cny"] for c in ledger["calls"])
            if used + RESERVATION > CAP:
                parser.error("已有账本的剩余预算不足以预留下一次请求")
            print(f"实时模式：仅合成数据，共用现有总上限 ¥{CAP/1e6:.2f}，已记保守费用 ¥{used/1e6:.6f}。")
            print("只有点击提交才执行新问题；同一 live 数据库内未完成任务也可能恢复执行。")
            key = getpass.getpass("个人 DeepSeek API Key（不回显、不落盘）：").strip()
            if not key:
                parser.error("Key 不能为空")
            worker_env["COMMERCE_PERSONAL_DEEPSEEK_API_KEY"] = key
            del key
        server = worker = None
        url = "http://127.0.0.1:" + str(args.port)
        client = Client(url, (runtime / "user.token").read_text())
        def interrupted(*_):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupted)
        with (nullcontext(None) if args.console_logs else (runtime / "java.log").open("ab")) as java_log, \
             (nullcontext(None) if args.console_logs else (runtime / "worker.log").open("ab")) as worker_log:
            try:
                cmd = server_command(runtime, args.port) + ["--commerce.demo.enabled=true", "--commerce.demo.agent-enabled=" + str(args.live).lower()]
                if args.owner:cmd += ["--commerce.owner.enabled=true","--commerce.owner.origin="+origin,
                    "--server.address="+args.bind,"--server.servlet.session.cookie.secure="+str(origin.startswith("https://")).lower()]
                server_env = environment()
                if args.postgres:
                    names = ("COMMERCE_DB_URL", "COMMERCE_DB_USER") + (() if args.db_secrets else ("COMMERCE_DB_PASSWORD",))
                    if any(not os.environ.get(name) for name in names):
                        parser.error("PostgreSQL 模式缺少显式数据库配置")
                    server_env.update({name: os.environ[name] for name in names})
                    cmd += ["--spring.profiles.active=postgres"]
                    if args.db_secrets:
                        secrets_dir = args.db_secrets.resolve()
                        if not (secrets_dir / "COMMERCE_DB_PASSWORD").is_file(): parser.error("数据库秘密文件缺失")
                        cmd += ["--spring.config.import=configtree:" + str(secrets_dir) + "/"]
                server = subprocess.Popen(cmd, cwd=ROOT, env=server_env, stdout=java_log, stderr=java_log, start_new_session=True)
                deadline = time.monotonic() + 45
                while True:
                    if server.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Java 启动失败，请检查项目 .local/web-demo 下的 java.log")
                    try:
                        client.call("/health", method="GET")
                        break
                    except OSError:
                        time.sleep(.15)
                worker = subprocess.Popen([sys.executable, "-m", "commerce_worker.worker", "--url", url,
                    "--token-file", str(runtime / "worker.token"), "--timeout", "180"], cwd=ROOT,
                    env=worker_env, stdout=worker_log, stderr=worker_log, start_new_session=True)
                worker_env.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)
                print("Demo ready: " + (origin+"/login" if args.owner else url+"/demo/index.html"), flush=True)
                print("模式：" + ("个人实时 Agent" if args.live else "指标查询 + 真实模型历史报告（无付费调用）"), flush=True)
                print("Ctrl+C 停止本次启动的服务，任务历史保留在项目运行目录。", flush=True)
                while server.poll() is None and worker.poll() is None:
                    time.sleep(.5)
                raise RuntimeError("Demo 子进程已退出，请检查项目内日志并重启")
            except KeyboardInterrupt:
                print("正在停止本次 Demo…", flush=True)
            finally:
                worker_env.pop("COMMERCE_PERSONAL_DEEPSEEK_API_KEY", None)
                stop_group(worker)
                stop_group(server)


if __name__ == "__main__":
    main()
