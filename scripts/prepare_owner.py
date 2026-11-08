"""Set this application's owner password, never a GitHub password or model key."""
import argparse
import getpass
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.java_local import environment,java,prepare
from scripts.state_guard import uses_state


def password_hash(password):
    if not 16<=len(password.encode())<=72 or any(c in password for c in "\r\n"):
        raise ValueError("password_requires_16_to_72_utf8_bytes")
    result=subprocess.run([java(),"-Dloader.main=dev.commerceinsight.PasswordHash","-cp",
        str(ROOT/"services/business-api/target/business-api-0.1.0.jar"),"org.springframework.boot.loader.launch.PropertiesLauncher"],
        cwd=ROOT,env=environment(),input=password+"\n",text=True,capture_output=True,check=True,timeout=30)
    value=result.stdout.strip()
    if not value.startswith("{bcrypt}$2a$12$") or len(value)!=68:raise ValueError("invalid_hasher_output")
    return value


@uses_state
def main():
    parser=argparse.ArgumentParser(description="设置本应用 owner 密码，只保存 BCrypt 哈希")
    parser.add_argument("--runtime",type=Path,default=ROOT/".local/owner-demo/offline")
    parser.add_argument("--rotate",action="store_true",help="替换既有密码哈希；运行中的服务需要重启以生效")
    args=parser.parse_args();runtime=prepare(args.runtime);path=runtime/"owner.hash"
    if path.exists() and not args.rotate:parser.error("密码已设置；需要替换时显式使用 --rotate")
    if not sys.stdin.isatty():parser.error("请在自己的交互终端输入密码，不通过参数、文件或聊天发送")
    password=getpass.getpass("本应用 owner 密码（16–72 UTF-8 字节，不回显）：")
    if getpass.getpass("再次输入：")!=password:parser.error("两次密码不一致")
    try:value=password_hash(password)
    except (ValueError,subprocess.SubprocessError):parser.error("密码长度不符合要求或哈希工具未构建；请检查项目构建")
    finally:del password
    temp=runtime/"owner.hash.new"
    with temp.open("x") as stream:
        os.chmod(temp,0o600);stream.write(value+"\n");stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)
    print("已保存应用 owner 密码哈希。账户：owner。未保存明文密码；与 GitHub/模型服务账户无关。")


if __name__=="__main__":main()
