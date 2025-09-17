#!/usr/bin/env python3
"""
Telegram bot that auto-generates WireGuard client configs.
Updated for python-telegram-bot v20+ (async API).
"""

import os
import sqlite3
import subprocess
from pathlib import Path
from io import BytesIO

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes

try:
    import qrcode
except Exception:
    qrcode = None

# ---------- Configuration (via ENV) ----------
TOKEN = os.getenv('TG_BOT_TOKEN')
ADMIN_CHAT_ID = os.getenv('TG_ADMIN_CHAT_ID')
DB_PATH = os.getenv('WG_BOT_DB', 'wg_bot.db')

SERVER_PUBLIC_IP = os.getenv('SERVER_PUBLIC_IP')
SERVER_WG_PORT = int(os.getenv('SERVER_WG_PORT', '51820'))
SERVER_PUBLIC_KEY = os.getenv('SERVER_PUBLIC_KEY')
SERVER_INTERFACE = os.getenv('SERVER_INTERFACE', 'wg0')

CLIENT_BASE = os.getenv('CLIENT_BASE', '10.0.0.')
CLIENT_START = int(os.getenv('CLIENT_START', '2'))
CLIENT_MAX = int(os.getenv('CLIENT_MAX', '254'))

APPLY_PEER = os.getenv('APPLY_PEER', 'false').lower() in ('1', 'true', 'yes')

if not TOKEN:
    raise SystemExit('Please set TG_BOT_TOKEN environment variable')
if not SERVER_PUBLIC_IP or not SERVER_PUBLIC_KEY:
    raise SystemExit('Please set SERVER_PUBLIC_IP and SERVER_PUBLIC_KEY environment variables')

# ---------- Database ----------
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()
cur.execute('''
CREATE TABLE IF NOT EXISTS peers (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE,
    private_key TEXT,
    public_key TEXT,
    address TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')
conn.commit()

# ---------- Utilities ----------

def gen_keypair():
    p = subprocess.run(['wg', 'genkey'], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError('wg genkey failed: ' + p.stderr)
    priv = p.stdout.strip()
    p2 = subprocess.run(['wg', 'pubkey'], input=priv, capture_output=True, text=True)
    if p2.returncode != 0:
        raise RuntimeError('wg pubkey failed: ' + p2.stderr)
    pub = p2.stdout.strip()
    return priv, pub


def next_free_ip():
    cur.execute('SELECT address FROM peers')
    used = {row[0] for row in cur.fetchall()}
    for i in range(CLIENT_START, CLIENT_MAX + 1):
        ip = f"{CLIENT_BASE}{i}"
        if ip not in used:
            return ip
    raise RuntimeError('No free IPs left in pool')


def make_conf(client_priv, client_ip):
    return f"""[Interface]
PrivateKey = {client_priv}
Address = {client_ip}/32
DNS = 1.1.1.1

[Peer]
PublicKey = {SERVER_PUBLIC_KEY}
Endpoint = {SERVER_PUBLIC_IP}:{SERVER_WG_PORT}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""


def apply_peer_to_interface(client_pub, client_ip):
    if not APPLY_PEER:
        return None
    cmd = ['wg', 'set', SERVER_INTERFACE, 'peer', client_pub, 'allowed-ips', f'{client_ip}/32']
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError('wg set failed: ' + p.stderr)
    return p.stdout

# ---------- Telegram Handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('Привет! Я генерирую WireGuard-конфиги. Используй /newclient <name>.')


async def newclient(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Использование: /newclient <client_name>')
        return
    name = context.args[0]
    try:
        priv, pub = gen_keypair()
        ip = next_free_ip()
    except Exception as e:
        await update.message.reply_text(f'Ошибка: {e}')
        return

    try:
        cur.execute('INSERT INTO peers(name, private_key, public_key, address) VALUES (?,?,?,?)', (name, priv, pub, ip))
        conn.commit()
    except sqlite3.IntegrityError:
        await update.message.reply_text('Клиент с таким именем уже существует.')
        return

    try:
        apply_peer_to_interface(pub, ip)
    except Exception as e:
        await update.message.reply_text(f'Не удалось применить к интерфейсу: {e}')

    conf_text = make_conf(priv, ip)
    bio = BytesIO(conf_text.encode())
    bio.name = f'{name}.conf'
    await update.message.reply_document(document=InputFile(bio), filename=bio.name, caption=f'Config {name} ({ip})')

    if qrcode:
        img = qrcode.make(conf_text)
        bio2 = BytesIO()
        img.save(bio2, format='PNG')
        bio2.seek(0)
        await update.message.reply_photo(photo=InputFile(bio2, filename=f'{name}.png'), caption='QR')


async def list_clients(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text('Только администратор может смотреть список клиентов.')
        return
    cur.execute('SELECT name, address, created_at FROM peers ORDER BY id')
    rows = cur.fetchall()
    if not rows:
        await update.message.reply_text('Нет клиентов')
        return
    lines = ['Список клиентов:'] + [f"{r[0]} — {r[1]} — {r[2]}" for r in rows]
    await update.message.reply_text('\n'.join(lines))


async def get_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Использование: /config <client_name>')
        return
    name = context.args[0]
    cur.execute('SELECT private_key, address FROM peers WHERE name=?', (name,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text('Клиент не найден')
        return
    priv, ip = row
    conf_text = make_conf(priv, ip)
    bio = BytesIO(conf_text.encode())
    bio.name = f'{name}.conf'
    await update.message.reply_document(document=InputFile(bio), filename=bio.name)
    if qrcode:
        img = qrcode.make(conf_text)
        bio2 = BytesIO()
        img.save(bio2, format='PNG')
        bio2.seek(0)
        await update.message.reply_photo(photo=InputFile(bio2, filename=f'{name}.png'))


async def revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text('Использование: /revoke <client_name>')
        return
    if ADMIN_CHAT_ID and str(update.effective_chat.id) != str(ADMIN_CHAT_ID):
        await update.message.reply_text('Только администратор может отзывать клиентов.')
        return
    name = context.args[0]
    cur.execute('SELECT public_key, address FROM peers WHERE name=?', (name,))
    row = cur.fetchone()
    if not row:
        await update.message.reply_text('Клиент не найден')
        return
    pub, ip = row
    if APPLY_PEER:
        try:
            subprocess.run(['wg', 'set', SERVER_INTERFACE, 'peer', pub, 'remove'], capture_output=True, text=True)
        except Exception as e:
            await update.message.reply_text(f'Не удалось удалить peer: {e}')
    cur.execute('DELETE FROM peers WHERE name=?', (name,))
    conn.commit()
    await update.message.reply_text(f'Клиент {name} ({ip}) удалён')


# ---------- Main ----------

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('newclient', newclient))
    app.add_handler(CommandHandler('list', list_clients))
    app.add_handler(CommandHandler('config', get_config))
    app.add_handler(CommandHandler('revoke', revoke))
    print('Bot started')
    app.run_polling()

if __name__ == '__main__':
    main()
