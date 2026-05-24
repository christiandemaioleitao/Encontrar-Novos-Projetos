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
import requests
from html.parser import HTMLParser
from datetime import datetime, timezone

# ─── Configurações ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

# START_ID só é usado se last_valid_id.json não existir
DEFAULT_START_ID = int(os.environ.get("START_ID", 50590))
MAX_EMPTY_CONSECUTIVE = 50
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
            return s.get("last_valid_id", DEFAULT_START_ID)
        except (json.JSONDecodeError, IOError, KeyError):
            pass
    return DEFAULT_START_ID

def save_state(current_id):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"last_valid_id": current_id}, f, indent=2)

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

    # Detecta redirecionamento para página de erro (ID inválido)
    if "InternalError" in r.url or "alvarafacil" not in r.url:
        return None

    # Detecta página inválida
    if "não encontrado" in html or ("Tipo Alvar" not in html and "Acompanha Alvar" not in html):
        return None

    # Parser HTML robusto — usa html.parser para extrair pares label → valor
    # A tabela tem estrutura <tr><td class="titulo">Label</td><td>Valor</td></tr>
    class FormExtractor:
        def __init__(self, html):
            self.fields = {}
            self._parse(html)

        def _parse(self, html):
            # Usa html.parser para walk no DOM sem regex greedy
            class TableParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.in_titulo_td = False
                    self.in_valor_td = False
                    self.current_label = None
                    self.fields = {}
                    self.td_count = 0

                def handle_starttag(self, tag, attrs):
                    if tag == "td":
                        cls = dict(attrs).get("class", "")
                        if "titulo" in cls.lower():
                            self.in_titulo_td = True
                            self.in_valor_td = False
                        elif self.in_titulo_td and not self.in_valor_td:
                            # Este td é o de valor (imediatamente após o label)
                            self.in_valor_td = True
                            self.in_titulo_td = False

                def handle_data(self, data):
                    stripped = data.strip()
                    if not stripped or re.match(r"^&nbsp;$|^&#\d+;$|^&#x[a-f0-9]+;$", stripped, re.I):
                        return
                    if self.in_titulo_td:
                        self.current_label = stripped
                        self.in_titulo_td = False
                    elif self.in_valor_td and self.current_label:
                        # Só grava se ainda não existe (evita sobrescrever comradio buttons)
                        if self.current_label not in self.fields:
                            self.fields[self.current_label] = stripped
                        self.current_label = None
                        self.in_valor_td = False

            parser = TableParser()
            parser.feed(html)
            self.fields = parser.fields

        def get(self, key):
            return self.fields.get(key, "")

    fe = FormExtractor(html)

    return {
        "id":            project_id,
        "url":           url,
        "situacao":      fe.get("Situação")                                       or "—",
        "taxa_data":     fe.get("Data Pagamento Taxa Inicial")                   or "—",
        "licenca_previa": fe.get("Licença Prévia")                                or "—",
        "tipo":          fe.get("Tipo")                                           or "—",
        "autor":         fe.get("Autor")                                           or "—",
        "proprietario":   fe.get("Proprietário")                                   or "—",
        "endereco":       fe.get("Endereço")                                      or "—",
        "num_pav":        fe.get("Número de Pavimentos")                          or "—",
        "area_terreno":   fe.get("Área Terreno")                                  or "—",
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
    start_id = load_state()
    current_id = start_id
    consecutive_empty = 0
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
            save_state(current_id)
        else:
            print(f"  #{current_id} ❌ vazio")
            consecutive_empty += 1

        current_id += 1
        time.sleep(REQUEST_DELAY)

    print(f"\n✅ Scan terminado: {tested} IDs testados, {found} encontrados")

    if new_projects:
        save_state(current_id - 1)
        msg = build_message(new_projects)
        send_telegram(msg)
    else:
        save_state(current_id - 1)
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