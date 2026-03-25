#!/usr/bin/env python3
"""
Skript pro stažení správných odpovědí přes Anthropic API.
Použití: ANTHROPIC_API_KEY=sk-ant-... python generate_answers.py
"""
import re, json, os, sys, time, urllib.request, urllib.error
from pathlib import Path

QUESTIONS_FILE = Path(__file__).parent / "zkusebni_otazky.txt"
ANSWERS_FILE   = Path(__file__).parent / "answers.json"
API_URL        = "https://api.anthropic.com/v1/messages"
BATCH_SIZE     = 15

def parse_questions(text):
    text = re.sub(r'Aktualizováno dne.*?\n', '', text)
    text = re.sub(r'\n\d+\n', '\n', text)
    text = re.sub(r'\f', '', text)

    okruhy_map = {}
    okruh_pattern = re.compile(r'^(\d+)\. okruh\s*[–-]\s*(.+)$', re.MULTILINE)
    zakon_pattern  = re.compile(r'^(Zákon|Nařízení|Usnesení|Služební předpis|Zákoník).*$', re.MULTILINE)
    okruhy = list(okruh_pattern.finditer(text))
    zakony = list(zakon_pattern.finditer(text))

    def get_context(pos):
        okruh_name, okruh_num, zakon_name = "Neznámý okruh", 0, ""
        for m in okruhy:
            if m.start() <= pos:
                okruh_name, okruh_num = m.group(2).strip(), int(m.group(1))
        for m in zakony:
            if m.start() <= pos:
                zakon_name = m.group(0).strip()
        return okruh_num, okruh_name, zakon_name

    q_pattern = re.compile(
        r'^(\d+)\.\s+(.+?)\na\)\s+(.+?)\nb\)\s+(.+?)\nc\)\s+(.+?)(?=\n\d+\.\s|\Z)',
        re.MULTILINE | re.DOTALL
    )
    questions = []
    for m in q_pattern.finditer(text):
        num = int(m.group(1))
        okruh_num, okruh_name, zakon = get_context(m.start())
        questions.append({
            "id": num, "okruh_num": okruh_num, "okruh": okruh_name, "zakon": zakon,
            "question": ' '.join(m.group(2).split()),
            "options": {
                "a": ' '.join(m.group(3).split()),
                "b": ' '.join(m.group(4).split()),
                "c": ' '.join(m.group(5).split())
            }
        })
    return sorted(questions, key=lambda x: x["id"])

def call_api(messages, system="", api_key=""):
    payload = {"model": "claude-sonnet-4-20250514", "max_tokens": 1000, "messages": messages}
    if system:
        payload["system"] = system
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL, data=data,
        headers={"Content-Type": "application/json", "x-api-key": api_key,
                 "anthropic-version": "2023-06-01"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))["content"][0]["text"]

def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("❌ Chybí API klíč. Nastav: export ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    raw = QUESTIONS_FILE.read_text(encoding="utf-8")
    questions = parse_questions(raw)
    print(f"Načteno {len(questions)} otázek.")

    answers = {}
    if ANSWERS_FILE.exists():
        answers = json.loads(ANSWERS_FILE.read_text(encoding="utf-8"))

    missing = [q for q in questions if str(q['id']) not in answers]
    if not missing:
        print(f"✓ Všechny odpovědi už máš ({len(answers)} ks).")
        return

    print(f"Stahuju odpovědi pro {len(missing)} otázek...")

    system = """Jsi expert na české právo a státní správu.
Dostaneš otázky z úřednické zkoušky ČR s možnostmi a/b/c. Jedna je správná.
Odpověz POUZE čistým JSON objektem: {"1": "c", "2": "a", ...}
Žádný jiný text ani markdown."""

    for i in range(0, len(missing), BATCH_SIZE):
        batch = missing[i:i+BATCH_SIZE]
        lines = []
        for q in batch:
            lines.append(f"Otázka {q['id']} ({q['zakon'][:60] if q['zakon'] else q['okruh'][:60]}):")
            lines.append(f"  {q['question']}")
            for l, t in q['options'].items():
                lines.append(f"  {l}) {t}")
            lines.append("")

        prompt = "Urči správnou odpověď:\n\n" + "\n".join(lines) + "\nOdpověz pouze JSON objektem."
        print(f"  Dávka {i//BATCH_SIZE+1}/{(len(missing)+BATCH_SIZE-1)//BATCH_SIZE}: ot. {batch[0]['id']}–{batch[-1]['id']}...", end="", flush=True)

        try:
            resp = call_api([{"role": "user", "content": prompt}], system, api_key)
            m = re.search(r'\{[^{}]+\}', resp, re.DOTALL)
            if m:
                batch_ans = json.loads(m.group())
                answers.update({str(k): v.lower().strip() for k, v in batch_ans.items()})
                print(f" ✓ ({len([q for q in batch if str(q['id']) in answers])}/{len(batch)})")
            else:
                print(f" ⚠ JSON nenalezen")
        except Exception as e:
            print(f" ✗ {e}")

        ANSWERS_FILE.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(0.5)

    print(f"\n✓ Hotovo! Uloženo {len(answers)} odpovědí do {ANSWERS_FILE}")

if __name__ == "__main__":
    main()
