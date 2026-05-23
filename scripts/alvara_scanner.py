#!/usr/bin/env python3
"""
Scanner de Alvarás - Prefeitura de Goiânia
Varredura diária de novos projetos de Alvará de Construção.
Hospedado no GitHub Actions (executado 1x por dia).
Se encontrar novos projetos, envia resumo via Telegram.

Uso:
  START_ID=49125 TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 alvara_scanner.py
"""

import os
import sys
import json
import time
import re
import requests
from datetime import datetime, timezone

# ─── Configurações ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

# START_ID só é usado se last_valid_id.json não existir
DEFAULT_START_ID = int(os.environ.get("START_ID", 49125))
MAX_EMPTY_CONSECUTIVE = 5
REQUEST_TIMEOUT = 20        # segundos por requisição
REQUEST_DELAY   = 1.5      # pausa entre requisições (segundos)
STATE_FILE      = "scripts/last_valid_id.json"
LOG_FILE        = "scripts/scan_log.json"
ALVARA_BASE     = "https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx"
TIPO_ALVARA     = 2
# ─────────────────────────────────────────────────────────────────


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                s = json.load(f)
            return s.get("last_valid_id", DEFAULT_START_ID), s.get("consecutive_empty", 0)
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_START_ID, 0

def save_state(current_id, consecutive_empty):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"last_valid_id": current_id, "consecutive_empty": consecutive_empty}, f, indent=2)

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token/Chat ID não configurado — pulando.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_THREAD_ID
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.ok:
            print("[TELEGRAM] Mensagem enviada!")
            return True
        else:
            print(f"[TELEGRAM] Erro {r.status_code}: {r.text}")
            return False
    except requests.RequestException as e:
        print(f"[TELEGRAM] Exceção: {e}")
        return False

def fetch_project(project_id):
    """Retorna dict com dados do projeto ou None se inválido/não encontrado."""
    url = f"{ALVARA_BASE}?ProjetoId={project_id}&TipoAlvara={TIPO_ALVARA}"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
    except requests.RequestException as e:
        print(f"  [rede] erro: {e}")
        return None  # erro de rede ≠ página vazia

    if r.status_code != 200:
        return None

    # Detecta página inválida
    if "não encontrado" in html or ("Tipo Alvar" not in html):
        return None

    def extract_all(label):
        pattern = rf"{re.escape(label)}.*?<td[^>]*>(.*?)</td>"
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return ""

    situacao       = extract_all("Situação")                            or "—"
    taxa           = extract_all("Data Pagamento Taxa Inicial")          or "—"
    licenca_previa = extract_all("Licença Prévia")                       or "—"
    tipo           = extract_all("Tipo")                                 or "—"
    proprietario   = extract_all("Proprietário")                         or "—"
    endereco       = extract_all("Endereço")                             or "—"
    pavimentos     = extract_all("Descrição de Pavimentos")            or "—"
    num_pav        = extract_all("Número de Pavimentos")                or "—"
    area_terreno   = extract_all("Área Terreno")                        or "—"

    return {
        "id":            project_id,
        "url":           url,
        "situacao":      situacao,
        "taxa_data":     taxa,
        "licenca_previa": licenca_previa,
        "tipo":          tipo,
        "proprietario":  proprietario,
        "endereco":      endereco,
        "pavimentos":    pavimentos,
        "num_pav":       num_pav,
        "area_terreno":  area_terreno,
    }

def build_message(projects, run_id=None):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y às %H:%M UTC")
    lines = [
        f"🆕 <b>Novos Alvarás Detectados</b>",
        f"📅 {now}",
        f"🔢 {len(projects)} projeto(s) novo(s) — ID #{projects[0]['id']} a #{projects[-1]['id']}",
        "",
    ]
    for p in projects:
        lines += [
            f"───",
            f"📋 <b>Projeto #{p['id']}</b>",
            f"🔗 <a href=\"{p['url']}\">Acompanhar Projeto</a>",
            f"📌 Status: {p['situacao']}",
        ]
        if p.get("taxa_data") and p["taxa_data"] not in ("—",""):
            lines.append(f"💳 Taxa Inicial: {p['taxa_data']}")
        if p.get("tipo") and p["tipo"] not in ("—",""):
            lines.append(f"🏗️ Tipo: {p['tipo']}")
        if p.get("endereco") and p["endereco"] not in ("—",""):
            lines.append(f"📍 Endereço: {p['endereco']}")
        if p.get("proprietario") and p["proprietario"] not in ("—",""):
            lines.append(f"👤 Proprietário: {p['proprietario']}")
        if p.get("num_pav") and p["num_pav"] not in ("—",""):
            lines.append(f"🏢 Pavimentos: {p['num_pav']}")
        if p.get("area_terreno") and p["area_terreno"] not in ("—",""):
            lines.append(f"📐 Área Terreno: {p['area_terreno']}")
        if p.get("pavimentos") and p["pavimentos"] not in ("—",""):
            lines.append(f"   {p['pavimentos']}")
        if p.get("licenca_previa") and p["licenca_previa"] not in ("—",""):
            lines.append(f"📄 Licença Prévia: {p['licenca_previa']}")
        if p.get("autor") and p["autor"] not in ("—",""):
            lines.append(f"🏢 Autor: {p['autor']}")
        lines.append("")
    return "\n".join(lines)

def run():
    start_id, initial_empty = load_state()
    current_id = start_id
    consecutive_empty = initial_empty
    new_projects = []
    tested = 0
    found = 0

    print(f"▶ Partindo do ID {current_id}  (vazios consecutivos: {consecutive_empty})")

    while consecutive_empty < MAX_EMPTY_CONSECUTIVE:
        tested += 1
        result = fetch_project(current_id)

        if result is not None:
            print(f"  #{current_id} ✅  [{result['situacao']}]")
            consecutive_empty = 0
            new_projects.append(result)
            found += 1
            save_state(current_id, consecutive_empty)
        else:
            print(f"  #{current_id} ❌ vazio")
            consecutive_empty += 1
            # Não salva estado aqui — só salva quando encontrar algo
            # (evita sobrescrever com vazios)

        current_id += 1  # SEMPRE incrementa
        time.sleep(REQUEST_DELAY)

    print(f"\n✅ Scan terminado: {tested} IDs testados, {found} encontrados")

    if new_projects:
        save_state(current_id - 1, consecutive_empty)  # salva último válido
        msg = build_message(new_projects)
        send_telegram(msg)
    else:
        save_state(current_id - 1, consecutive_empty)
        print("Nenhum projeto novo — sem envio.")

    # Log da execução
    log_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "start_id": start_id,
        "end_id": current_id - 1,
        "tested": tested,
        "found": found,
    }
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE) as f:
                logs = json.load(f)
        except json.JSONDecodeError:
            logs = []
    else:
        logs = []
    logs.append(log_entry)
    logs = logs[-30:]  # mantém só últimos 30
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    return 0

if __name__ == "__main__":
    sys.exit(run())