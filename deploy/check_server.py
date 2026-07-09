import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("185.199.196.104", username="root", password="SRqVImGA3kDaZ", timeout=30)

cmds = [
    "systemctl is-active progrever",
    "journalctl -u progrever -n 25 --no-pager",
    "test -f /opt/progrever/.env && echo HAS_ENV || echo NO_ENV",
    "grep -E '^(BOT_TOKEN|API_ID|ADMIN_IDS)=' /opt/progrever/.env",
    "ls -la /opt/progrever/sessions/ 2>/dev/null | head -5",
]

for cmd in cmds:
    print("===", cmd, "===")
    _, out, err = c.exec_command(cmd)
    text = out.read().decode("utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith("BOT_TOKEN="):
            print("BOT_TOKEN=***hidden***")
        else:
            print(line)
    e = err.read().decode("utf-8", errors="replace")
    if e.strip():
        print("ERR:", e)

c.close()
