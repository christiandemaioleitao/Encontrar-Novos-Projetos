"""
Scanner de Alvarás - Prefeitura de Goiânia
Varredura diária de novos projetos de Alvará de Construção.
Hospedado no GitHub Actions (executado 1x por dia).
Se encontrar novos projetos, envia resumo via Telegram.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime

# ─── Configurações ───────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")
TELEGRAM_THREAD_ID = os.environ.get("TELEGRAM_THREAD_ID", "")

ALVARA_BASE = "https://www10.goiania.go.gov.br/alvarafacil/AcompanhaAprovacaoProjeto.aspx"
TIPO_ALVARA = 2  # Alvará de Construção

# ID mínimo para iniciar a varredura (último válido conhecido)
START_ID = int(os.environ.get("START_ID", 49125))
MAX_EMPTY_CONSECUTIVE = 5

# Estado persiste entre execuções via arquivo no github workspace
STATE_FILE = "last_valid_id.json"
LOG_FILE   = "scan_log.json"
# ─────────────────────────────────────────────────────────────────


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"last_valid_id": START_ID, "consecutive_empty": 0, "new_found_ids": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def send_telegram(message, reply_to=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Token/Chat ID não configurado — pulando envio.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    if TELEGRAM_THREAD_ID:
        payload["message_thread_id"] = TELEGRAM_THREAD_ID
    if reply_to:
        payload["reply_to_message_id"] = reply_to

    r = requests.post(url, json=payload, timeout=15)
    if r.ok:
        print(f"[TELEGRAM] Mensagem enviada com sucesso!")
    else:
        print(f"[TELEGRAM] Erro ao enviar: {r.status_code} — {r.text}")

def fetch_project(project_id):
    """Faz requisição GET e retorna dict com dados do projeto."""
    url = f"{ALVARA_BASE}?ProjetoId={project_id}&TipoAlvara={TIPO_ALVARA}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
    except requests.RequestException as e:
        print(f"[{project_id}] Erro de rede: {e}")
        return None

    if r.status_code != 200:
        return None

    # Páginas inválidas retornam "Tipo Alvará não encontrado"
    if "não encontrado" in html or "Tipo Alvar" not in html:
        return None

    # Extrai campos principais por regex
    def extract(label):
        import re
        pattern = rf"{label}.*?<td[^>]*>(.*?)</td>"
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m:
            return re.sub(r"<[^>]+>", "", m.group(1)).strip()
        return ""

    situacao = extract("Situação")
    if not situacao:
        situacao = "—"

    taxa = extract("Data Pagamento Taxa Inicial") or "—"
    licenca_previa = extract("Licença Prévia") or "—"
    autor = extract("Autor") or "—"

    return {
        "id": project_id,
        "url": url,
        "situacao": situacao,
        "taxa_data": taxa,
        "licenca_previa": licenca_previa,
        "autor": autor,
    }

def is_valid_project(project_id):
    """Verifica se o ID retorna um projeto válido (sem fazer parse completo)."""
    url = f"{ALVARA_BASE}?ProjetoId={project_id}&TipoAlvara={TIPO_ALVARA}"
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
        return r.status_code == 200 and "não encontrado" not in html and "Tipo Alvar" in html
    except requests.RequestException:
        return False

def build_telegram_message(projects):
    """Monta mensagem formatada em HTML para o Telegram."""
    now = datetime.utcnow().strftime("%d/%m/%Y %H:%M UTC")
    lines = [
        f"🆕 <b>Novos Alvarás Detectados</b>",
        f"📅 {now}",
        f"🔢 {len(projects)} novo(s) projeto(s) encontrado(s)",
        "",
    ]
    for p in projects:
        lines.append(f"───")
        lines.append(f"📋 <b>Projeto #{p['id']}</b>")
        lines.append(f"🔗 <a href=\"{p['url']}\">Acompanhar Projeto</a>")
        lines.append(f"📌 Status: {p['situacao']}")
        if p.get("taxa_data") and p["taxa_data"] != "—":
            lines.append(f"💳 Taxa Inicial: {p['taxa_data']}")
        if p.get("licenca_previa") and p["licenca_previa"] != "—":
            lines.append(f"📄 Licença Prévia: {p['licenca_previa']}")
        if p.get("autor") and p["autor"] != "—":
            lines.append(f"🏢 {p['autor']}")
        lines.append("")

    return "\n".join(lines)

def run():
    print("=" * 60)
    print("  🔍 SCANNER DE ALVARÁS — Prefeitura de Goiânia")
    print("=" * 60)
    print(f"  Start ID : {START_ID}")
    print(f"  Max vazios consecutivos: {MAX_EMPTY_CONSECUTIVE}")
    print(f"  Telegram  : {'✅ configurado' if TELEGRAM_BOT_TOKEN else '❌ não configurado'}")
    print("=" * 60)

    state = load_state()
    current_id = state["last_valid_id"]
    consecutive_empty = state.get("consecutive_empty", 0)
    new_projects = []

    print(f"\n▶ Partindo do ID {current_id}\n")

    while consecutive_empty < MAX_EMPTY_CONSECUTIVE:
        project_id = current_id
        print(f"  Testando #{project_id}...", end=" ", flush=True)

        result = fetch_project(project_id)

        if result is not None:
            print("✅ ACHADO")
            consecutive_empty = 0
            state["last_valid_id"] = project_id

            new_projects.append(result)
            print(f"     Situação: {result['situacao']}")
            print(f"     Taxa: {result['taxa_data']}")
        else:
            print("❌ vazio")
            consecutive_empty += 1
            current_id += 1

        # Pausa polida entre requisições (não sobrecarregar o servidor)
        time.sleep(1.5)

    print(f"\n{'=' * 60}")
    print(f"  ✅ Varredura finalizada")
    print(f"  Último ID válido: {state['last_valid_id']}")
    print(f"  Projetos novos encontrados: {len(new_projects)}")
    print(f"  IDs vazios consecutivos: {consecutive_empty}")
    print(f"{'=' * 60}")

    save_state(state)

    if new_projects:
        print("\n📨 Enviando resumo para o Telegram...")
        msg = build_telegram_message(new_projects)
        send_telegram(msg)
    else:
        print("\n✅ Nenhum projeto novo encontrado — sem envio.")

    # Salva log da execução
    log_entry = {
        "ts": datetime.utcnow().isoformat(),
        "last_valid_id": state["last_valid_id"],
        "new_count": len(new_projects),
        "new_ids": [p["id"] for p in new_projects],
        "empty_consecutive": consecutive_empty,
    }
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f:
            logs = json.load(f)
    else:
        logs = []
    logs.append(log_entry)
    # Mantém só últimos 30 dias de log
    logs = logs[-30:]
    with open(LOG_FILE, "w") as f:
        json.dump(logs, f, indent=2)

    return 0 if not new_projects else 0

if __name__ == "__main__":
    sys.exit(run())