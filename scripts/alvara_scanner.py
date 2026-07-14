#!/usr/bin/env python3
"""
Scanner de Alvarás - Prefeitura de Goiânia
Varredura diária de novos projetos de Alvará de Construção.
Hospedado no GitHub Actions (executado 1x por dia).
Se encontrar novos projetos, envia resumo via Telegram.

Uso:
  START_ID=1430 TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 alvara_scanner.py
"""

import os
import sys
import json
import time
import re
import html as htmllib
import requests
from datetime import datetime, timezone

# ─── Configurações ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

# START_ID só é usado se last_valid_id.json não existir
DEFAULT_START_ID = int(os.environ.get("START_ID", 50590))
MAX_EMPTY_CONSECUTIVE = 50
# Teto de IDs testados por execução: evita estourar o timeout de 30 min do
# GitHub Actions quando há um backlog grande (a fila é limpa em rodadas diárias).
MAX_IDS_PER_RUN = int(os.environ.get("MAX_IDS_PER_RUN", 300))
# Parada por tempo de relógio: garante que a rodada nunca chega perto do
# timeout de 30 min, mesmo quando o site da Prefeitura está lento.
DEADLINE_SECONDS = int(os.environ.get("DEADLINE_SECONDS", 1200))   # 20 min
# Nº de projetos por mensagem do Telegram (limite da API é 4096 caracteres).
TELEGRAM_CHUNK  = 8
# Pausa entre mensagens do Telegram para não tomar 429 (Too Many Requests).
TELEGRAM_MSG_DELAY = 3.5
REQUEST_TIMEOUT = 20        # segundos por requisição
REQUEST_DELAY   = 1.5       # pausa entre requisições (segundos)
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
            return s.get("last_valid_id", DEFAULT_START_ID)
        except (json.JSONDecodeError, IOError, KeyError):
            pass
    return DEFAULT_START_ID

def save_state(current_id):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"last_valid_id": current_id}, f, indent=2)

def escape_html(text: str) -> str:
    """Escapa caracteres especiais para uso seguro em mensagens HTML do Telegram."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token/Chat ID não configurado — pulando.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,   # FIX: era "true" (string); deve ser booleano
    }
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_THREAD_ID
    # Até 4 tentativas, respeitando o retry_after quando a API devolve 429.
    for _ in range(4):
        try:
            r = requests.post(url, json=payload, timeout=15)
        except requests.RequestException as e:
            print(f"[TELEGRAM] Exceção: {e}")
            return False
        if r.ok:
            print("[TELEGRAM] Mensagem enviada!")
            return True
        if r.status_code == 429:
            wait = 5
            try:
                wait = int(r.json()["parameters"]["retry_after"]) + 1
            except Exception:
                pass
            print(f"[TELEGRAM] 429 — aguardando {wait}s e tentando de novo…")
            time.sleep(wait)
            continue
        print(f"[TELEGRAM] Erro {r.status_code}: {r.text}")
        return False
    print("[TELEGRAM] Falhou após várias tentativas (429).")
    return False

def extract_fields(raw_html: str) -> dict:
    """
    Extrai pares campo→valor da página de alvará.

    A página usa dois padrões diferentes de campo:
      1. <label>Campo</label></div><div...><span>Valor</span>
         → Usado em: Tipo, Autor, Situação, Data Pagamento Taxa Inicial, etc.
      2. <label>Campo</label><input ... value="Valor" ... readonly>
         → Usado em: Endereço, Proprietário, Nr de Pavimentos, Área Terreno, etc.

    FIX: o código anterior usava TableParser buscando <td class="titulo">,
    estrutura que NÃO EXISTE nessa página — resultando em todos os campos "—".
    """
    fields = {}

    # Padrão 1: <label>...</label></div> ... <span>VALOR</span>
    pat_span = (
        r'<label[^>]*>\s*([^<]+?)\s*</label\s*>\s*</div\s*>'   # label + fecha div
        r'.*?'                                                   # qualquer coisa
        r'<span[^>]*>\s*([^<]*?)\s*</span\s*>'                 # span com valor
    )
    for label, val in re.findall(pat_span, raw_html, re.S | re.I):
        label = label.strip()
        val   = htmllib.unescape(val).strip()
        if label and len(label) < 80:
            fields.setdefault(label, val)

    # Padrão 2: <label>...</label><input ... value="VALOR" ... readonly ...>
    pat_input = (
        r'<label[^>]*>\s*([^<]{1,80}?)\s*</label>\s*'
        r'<input[^>]+value="([^"]*)"[^>]+readonly'
    )
    for label, val in re.findall(pat_input, raw_html, re.I):
        label = label.strip()
        val   = htmllib.unescape(val).strip()
        if label and len(label) < 80:
            fields.setdefault(label, val)

    return fields

def fetch_project(project_id):
    """Retorna dict com dados do projeto ou None se inválido/não encontrado."""
    url = f"{ALVARA_BASE}?ProjetoId={project_id}&TipoAlvara={TIPO_ALVARA}"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        raw_html = r.text
    except requests.RequestException as e:
        print(f"  [rede] erro: {e}")
        return None  # erro de rede ≠ página vazia

    if r.status_code != 200:
        return None

    # Detecta redirecionamento para página de erro (ID inválido)
    if "InternalError" in r.url or "alvarafacil" not in r.url:
        return None

    # Detecta página inválida (sem conteúdo de alvará)
    if "não encontrado" in raw_html or "Acompanha Alvar" not in raw_html:
        return None

    fields = extract_fields(raw_html)

    # FIX: mapeamento das chaves corrigido para bater com os nomes reais da página
    return {
        "id":            project_id,
        "url":           url,
        "situacao":      fields.get("Situação")                     or "—",
        "taxa_data":     fields.get("Data Pagamento Taxa Inicial")  or "—",
        "tipo":          fields.get("Tipo")                         or "—",
        "autor":         fields.get("Autor")                        or "—",
        "proprietario":  fields.get("Proprietário")                 or "—",
        "endereco":      fields.get("Endereço")                     or "—",
        "num_pav":       fields.get("Nr de Pavimentos")             or "—",   # FIX: era "Número de Pavimentos"
        "area_terreno":  fields.get("Area terreno:  (m²)")          or "—",   # FIX: era "Área Terreno"
        "area_construir": fields.get("Área a ser construída (m²)") or "—",
    }

def build_message(projects):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y às %H:%M UTC")
    lines = [
        f"🆕 <b>Novos Alvarás Detectados</b>",
        f"📅 {now}",
        f"🔢 {len(projects)} projeto(s) novo(s) — ID #{projects[0]['id']} a #{projects[-1]['id']}",
        "",
    ]
    for p in projects:
        lines += [
            f"───────────────────",
            f"📋 <b>Projeto #{p['id']}</b>",
            f"🔗 <a href=\"{p['url']}\">Acompanhar Projeto</a>",
            # FIX: todos os valores escapados com escape_html() para evitar quebra do HTML mode
            f"📌 Status: {escape_html(p['situacao'])}",
        ]
        if p.get("taxa_data") and p["taxa_data"] not in ("—", ""):
            lines.append(f"💳 Taxa Inicial: {escape_html(p['taxa_data'])}")
        if p.get("tipo") and p["tipo"] not in ("—", ""):
            lines.append(f"🏗️ Tipo: {escape_html(p['tipo'])}")
        if p.get("endereco") and p["endereco"] not in ("—", ""):
            lines.append(f"📍 Endereço: {escape_html(p['endereco'])}")
        if p.get("proprietario") and p["proprietario"] not in ("—", ""):
            lines.append(f"👤 Proprietário: {escape_html(p['proprietario'])}")
        if p.get("num_pav") and p["num_pav"] not in ("—", ""):
            lines.append(f"🏢 Pavimentos: {escape_html(p['num_pav'])}")
        if p.get("area_terreno") and p["area_terreno"] not in ("—", ""):
            lines.append(f"📐 Área Terreno: {escape_html(p['area_terreno'])} m²")
        if p.get("area_construir") and p["area_construir"] not in ("—", ""):
            lines.append(f"🔨 Área a Construir: {escape_html(p['area_construir'])} m²")
        if p.get("autor") and p["autor"] not in ("—", ""):
            lines.append(f"✏️ Autor: {escape_html(p['autor'])}")
        lines.append("")
    return "\n".join(lines)

def run():
    last_id = load_state()
    # FIX: começa do próximo ID para não retestar o último já verificado
    current_id = last_id + 1
    consecutive_empty = 0
    new_projects = []
    tested = 0
    found = 0

    print(f"▶ Partindo do ID {current_id}  (último testado: {last_id})")

    t0 = time.time()
    # Para em 50 vazios seguidos, no teto de IDs, OU no deadline de tempo (anti-timeout).
    while (consecutive_empty < MAX_EMPTY_CONSECUTIVE
           and tested < MAX_IDS_PER_RUN
           and (time.time() - t0) < DEADLINE_SECONDS):
        tested += 1
        result = fetch_project(current_id)

        if result is not None:
            print(f"  #{current_id} ✅  [{result['situacao']}] {result['proprietario'][:40]}")
            consecutive_empty = 0
            new_projects.append(result)
            found += 1
            save_state(current_id)
        else:
            print(f"  #{current_id} ❌ vazio")
            consecutive_empty += 1

        current_id += 1
        time.sleep(REQUEST_DELAY)

    # Salva o último ID testado (mesmo que vazio) para retomar daqui
    save_state(current_id - 1)
    if (time.time() - t0) >= DEADLINE_SECONDS:
        parou_por = "deadline de tempo"
    elif tested >= MAX_IDS_PER_RUN:
        parou_por = "teto da rodada"
    else:
        parou_por = "50 vazios seguidos"
    print(f"\n✅ Scan terminado ({parou_por}): {tested} IDs testados, {found} encontrados")

    if new_projects:
        # Envia em lotes para não estourar o limite de 4096 caracteres do Telegram.
        for i in range(0, len(new_projects), TELEGRAM_CHUNK):
            batch = new_projects[i:i + TELEGRAM_CHUNK]
            send_telegram(build_message(batch))
            time.sleep(TELEGRAM_MSG_DELAY)
    else:
        print("Nenhum projeto novo — sem envio.")

    # Log da execução
    log_entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "start_id": last_id + 1,
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
