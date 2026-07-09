"""Deploy progrever to remote Ubuntu server via SSH/SFTP."""

import os
import sys
import stat

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import paramiko
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko", "-q"])
    import paramiko

HOST = "185.199.196.104"
USER = "root"
PASSWORD = "SRqVImGA3kDaZ"
REMOTE_DIR = "/opt/progrever"
LOCAL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SKIP = {
    ".git", "__pycache__", "venv", ".venv", "progrever.db",
    "*.pyc", ".cursor",
}


def should_skip(name: str) -> bool:
    if name in SKIP:
        return True
    if name.endswith(".pyc"):
        return True
    return False


def upload_dir(sftp, local: str, remote: str):
    for item in os.listdir(local):
        if should_skip(item):
            continue
        lp = os.path.join(local, item)
        rp = f"{remote}/{item}"
        if os.path.isdir(lp):
            try:
                sftp.mkdir(rp)
            except OSError:
                pass
            upload_dir(sftp, lp, rp)
        else:
            print(f"  upload: {item}")
            # Shell scripts must have Unix line endings on the server
            if item.endswith(".sh"):
                with open(lp, "rb") as f:
                    data = f.read().replace(b"\r\n", b"\n")
                with sftp.open(rp, "wb") as rf:
                    rf.write(data)
            else:
                sftp.put(lp, rp)


def run(client, cmd: str) -> tuple[int, str, str]:
    print(f"$ {cmd}")
    _, stdout, stderr = client.exec_command(cmd, get_pty=True)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out.strip())
    if err.strip():
        print(err.strip(), file=sys.stderr)
    return code, out, err


def main():
    print(f"Connecting to {HOST}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASSWORD, timeout=30)

    run(client, f"mkdir -p {REMOTE_DIR}/sessions {REMOTE_DIR}/media {REMOTE_DIR}/deploy")

    sftp = client.open_sftp()
    print("Uploading files...")
    upload_dir(sftp, LOCAL_DIR, REMOTE_DIR)
    sftp.close()

    print("Running server setup...")
    code, _, _ = run(client, f"chmod +x {REMOTE_DIR}/deploy/setup_server.sh && bash {REMOTE_DIR}/deploy/setup_server.sh")
    client.close()

    if code != 0:
        print(f"Setup failed with code {code}")
        sys.exit(code)

    print("\nDeploy complete!")
    print(f"Bot running at {HOST}")
    print("Check logs: journalctl -u progrever -f")


if __name__ == "__main__":
    main()
