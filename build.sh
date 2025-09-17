#!/usr/bin/env bash
set -e

echo "=== Installing WireGuard Telegram Bot ==="

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root"
   exit 1
fi

# 1. Dependencies
echo "[1/5] Installing system packages..."
apt-get update -y
apt-get install -y python3 python3-venv python3-pip wireguard-tools python3-python-telegram-bot python3-qrcode python3-willow

# 2. User
echo "[2/5] Creating user wg-bot..."
if ! id -u wg-bot >/dev/null 2>&1; then
    useradd -r -s /usr/sbin/nologin wg-bot
fi

# 3. Bot directory
INSTALL_DIR=/opt/wg-bot
mkdir -p $INSTALL_DIR
cp -r ./* $INSTALL_DIR
chown -R wg-bot:wg-bot $INSTALL_DIR

# Python venv
echo "[3/5] Setting up Python virtual environment..."
cd $INSTALL_DIR
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt

# 4. Environment variables
echo "[4/5] Configuring environment..."
ENV_FILE=/etc/wireguard/wg-bot.env

read -p "Enter TG_BOT_TOKEN: " TG_BOT_TOKEN
read -p "Enter SERVER_PUBLIC_IP (external IP): " SERVER_PUBLIC_IP
read -p "Enter (optional) TG_ADMIN_CHAT_ID: " TG_ADMIN_CHAT_ID

# Automatically fetch server public key
if wg show wg0 public-key >/dev/null 2>&1; then
    SERVER_PUBLIC_KEY=$(wg show wg0 public-key)
else
    echo "Error: cannot fetch public key from wg0, check your WireGuard setup."
    exit 1
fi

cat > $ENV_FILE <<EOF
TG_BOT_TOKEN=$TG_BOT_TOKEN
SERVER_PUBLIC_IP=$SERVER_PUBLIC_IP
SERVER_PUBLIC_KEY=$SERVER_PUBLIC_KEY
SERVER_WG_PORT=51820
SERVER_INTERFACE=wg0
APPLY_PEER=true
TG_ADMIN_CHAT_ID=$TG_ADMIN_CHAT_ID
EOF

chmod 600 $ENV_FILE
chown root:root $ENV_FILE

# 5. systemd service
echo "[5/5] Installing systemd service..."
SERVICE_FILE=/etc/systemd/system/wireguard-bot.service
cat > $SERVICE_FILE <<EOF
[Unit]
Description=Telegram WireGuard Bot
After=network.target

[Service]
Type=simple
User=wg-bot
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/wg_bot.py
Restart=on-failure
EnvironmentFile=$ENV_FILE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wireguard-bot.service

echo "=== Installation completed! ==="
echo "Check status: systemctl status wireguard-bot"
echo "Logs: journalctl -u wireguard-bot -f"
