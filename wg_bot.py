#!/usr/bin/env python3
import os
import subprocess
import qrcode
import io

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# === Load environment variables ===
BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
SERVER_PUBLIC_IP = os.getenv("SERVER_PUBLIC_IP")
SERVER_PUBLIC_KEY = os.getenv("SERVER_PUBLIC_KEY")
SERVER_WG_PORT = os.getenv("SERVER_WG_PORT", "51820")
SERVER_INTERFACE = os.getenv("SERVER_INTERFACE", "wg0")
APPLY_PEER = os.getenv("APPLY_PEER", "true").lower() == "true"
ADMIN_CHAT_ID = os.getenv("TG_ADMIN_CHAT_ID")

WG_CONFIG_DIR = "/etc/wireguard/clients"
os.makedirs(WG_CONFIG_DIR, exist_ok=True)


# === Helper functions ===
def generate_client_keys():
    """Generate private and public key pair for a new WireGuard client"""
    private_key = subprocess.check_output(["wg", "genkey"]).decode().strip()
    public_key = subprocess.check_output(
        ["wg", "pubkey"], input=private_key.encode()
    ).decode().strip()
    return private_key, public_key


def build_client_config(name: str, private_key: str) -> str:
    """Build client configuration file"""
    return f"""
[Interface]
PrivateKey = {private_key}
Address = 10.0.0.2/32
DNS = 1.1.1.1

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {SERVER_PUBLIC_IP}:{SERVER_WG_PORT}
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
"""


def save_client_config(name: str, config: str) -> str:
    """Save client configuration to file"""
    path = os.path.join(WG_CONFIG_DIR, f"{name}.conf")
    with open(path, "w") as f:
        f.write(config.strip())
    return path


def apply_peer_to_server(name: str, public_key: str):
    """Apply peer to the server using wg set"""
    subprocess.run(
        [
            "wg",
            "set",
            SERVER_INTERFACE,
            "peer",
            public_key,
            "allowed-ips",
            "10.0.0.2/32",
        ],
        check=True,
    )


def generate_qr_code(config: str) -> io.BytesIO:
    """Generate QR code from configuration"""
    img = qrcode.make(config)
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    bio.seek(0)
    return bio


# === Bot handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /newclient <name> to create a new client.")


async def new_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /newclient <name>")
        return

    name = context.args[0]
    priv, pub = generate_client_keys()
    config = build_client_config(name, priv)
    path = save_client_config(name, config)

    if APPLY_PEER:
        apply_peer_to_server(name, pub)

    # Send configuration file
    with open(path, "rb") as f:
        await update.message.reply_document(f, filename=f"{name}.conf")

    # Send QR code
    qr = generate_qr_code(config)
    await update.message.reply_photo(qr)


async def list_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("Access denied.")
        return

    files = os.listdir(WG_CONFIG_DIR)
    if not files:
        await update.message.reply_text("No clients found.")
    else:
        await update.message.reply_text("\n".join(files))


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text("Access denied.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /revoke <name>")
        return

    name = context.args[0]
    path = os.path.join(WG_CONFIG_DIR, f"{name}.conf")

    if not os.path.exists(path):
        await update.message.reply_text("Client not found.")
        return

    os.remove(path)
    await update.message.reply_text(f"Client {name} revoked (file removed).")


# === Main entrypoint ===
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newclient", new_client))
    app.add_handler(CommandHandler("list", list_clients))
    app.add_handler(CommandHandler("revoke", revoke))

    app.run_polling()


if __name__ == "__main__":
    main()
