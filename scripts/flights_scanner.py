#!/usr/bin/env python3
"""
Scanner de Passagens Aéreas — Goiânia / Brasília → Rio de Janeiro
Rodar diariamente via GitHub Actions (3:00 AM Brasília = 6:00 UTC)
Envia resultado via Telegram.
"""

import os
import json
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

# Rotas
ROTAS = [
    {"origem": "GYN", "cidade_origem": "Goiânia", "dest": "RIO", "cidade_dest": "Rio de Janeiro"},
    {"origem": "BSB", "cidade_origem": "Brasília",  "dest": "RIO", "cidade_dest": "Rio de Janeiro"},
]

# Google Flights — gera URL de busca
def google_flights_url(origem, destino):
    params = urllib.parse.urlencode({
        "q": f"voos de {origem} para {destino}",
        "hl": "pt-BR",
        "curr": "BRL",
    })
    return f"https://www.google.com/travel/flights?{params}"

# Links diretos das companhias
LINKS_COMPRAS = {
    "GYN-RIO": [
        ("GOL",   "https://www.voegol.com.br/busca-passagens?origin=GYN&destination=RIO&isRoundTrip=false&adults=1&children=0&infants=0&currency=BRL"),
        ("LATAM", "https://www.latamairlines.com/br/pt/passagens-aereas?origin=GYN&destination=RIO&adt=1&chd=0&inf=0&cabin=Economy&sort=TOTAL_FARE,asc"),
        ("Azul",  "https://www.azul.com.br/passagens-aereas/?origin=GYN&destination=RIO&tripType=O&paxType=ADT&cabinClass=ECONOMY"),
    ],
    "BSB-RIO": [
        ("GOL",   "https://www.voegol.com.br/busca-passagens?origin=BSB&destination=RIO&isRoundTrip=false&adults=1&children=0&infants=0&currency=BRL"),
        ("LATAM", "https://www.latamairlines.com/br/pt/passagens-aereas?origin=BSB&destination=RIO&adt=1&chd=0&inf=0&cabin=Economy&sort=TOTAL_FARE,asc"),
        ("Azul",  "https://www.azul.com.br/passagens-aereas/?origin=BSB&destination=RIO&tripType=O&paxType=ADT&cabinClass=ECONOMY"),
    ],
}

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Credenciais não configuradas.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                print("[TELEGRAM] Mensagem enviada!")
                return True
            print(f"[TELEGRAM] Erro {resp.status}")
            return False
    except Exception as e:
        print(f"[TELEGRAM] Exceção: {e}")
        return False

def run():
    today = datetime.now(timezone.utc).strftime("%d/%m/%Y às %H:%M UTC")
    lines = [
        f"✈️ <b>Passagens Aéreas — Rotas do Dia</b>",
        f"📅 {today}",
        f"🔍 GYN → RIO | BSB → RIO",
        "",
    ]

    for rota in ROTAS:
        key = f"{rota['origem']}-{rota['dest']}"
        lines += [
            "",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"🛫 <b>{rota['cidade_origem']} ({rota['origem']})</b> ➜ <b>{rota['cidade_dest']} ({rota['dest']})</b>",
            "",
        ]

        # Google Flights
        gf_url = google_flights_url(rota['origem'], rota['dest'])
        lines.append(f"🔗 <a href=\"{gf_url}\">Google Flights — preços em tempo real</a>")
        lines.append("")

        # Links diretos
        lines.append("💺 Comprar direto nas companhias:")
        for nome, link in LINKS_COMPRAS.get(key, []):
            lines.append(f"   • <a href=\"{link}\">{nome}</a>")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━",
        "⚠️ <i>Clique nos links para ver preços atualizados.</i>",
        "<b>🔔 Alerta automático — rodando diariamente às 3h (Brasília).</b>",
    ]

    msg = "\n".join(lines)
    send_telegram(msg)
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(run())
