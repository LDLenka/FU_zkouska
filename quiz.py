#!/usr/bin/env python3
"""
Kvíz pro přípravu na obecnou část úřednické zkoušky.

Použití:
  python quiz.py --get-answers     # Stáhne správné odpovědi přes API (jen jednou)
  python quiz.py                   # Spustí kvíz (interaktivní)
  python quiz.py --stats           # Zobrazí statistiky
  python quiz.py --okruh 1        # Jen otázky z okruhu 1
  python quiz.py --slabe          # Jen otázky kde máš < 60% úspěšnost
"""

import re
import json
import os
import sys
import random
import argparse
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

# ─── Konfigurace ──────────────────────────────────────────────────────────────
QUESTIONS_PDF = Path(__file__).parent / "zkusebni_otazky.txt"
ANSWERS_FILE  = Path(__file__).parent / "answers.json"
PROGRESS_FILE = Path(__file__).parent / "progress.json"
API_URL       = "https://api.anthropic.com/v1/messages"
BATCH_SIZE    = 15   # otázek na jedno API volání

# ─── Barvy terminálu ───────────────────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    GRAY   = "\033[90m"
    BG_GREEN = "\033[42m"
    BG_RED   = "\033[41m"

def c(color, text): return f"{color}{text}{C.RESET}"

# ─── Parsování otázek z PDF textu ──────────────────────────────────────────────
def parse_questions(text: str) -> list[dict]:
    """Parsuje otázky ze surového textu PDF."""
    # Odstranění hlavičky, číslování stránek
    text = re.sub(r'Aktualizováno dne.*?\n', '', text)
    text = re.sub(r'\n\d+\n', '\n', text)
    text = re.sub(r'\f', '', text)

    # Identifikace okruhů a zákonů
    okruh_pattern = re.compile(r'^(\d+)\. okruh\s*[–-]\s*(.+)$', re.MULTILINE)
    zakon_pattern  = re.compile(r'^(Zákon|Nařízení|Usnesení|Služební předpis|Zákoník).*$', re.MULTILINE)

    # Mapování čísla otázky → okruh + zákon (scan ahead)
    okruhy = list(okruh_pattern.finditer(text))
    zakony = list(zakon_pattern.finditer(text))

    def get_context(pos):
        okruh_name = "Neznámý okruh"
        okruh_num  = 0
        okruh_pos  = 0
        zakon_name = ""
        for m in okruhy:
            if m.start() <= pos:
                okruh_name = m.group(2).strip()
                okruh_num  = int(m.group(1))
                okruh_pos  = m.start()
        # Zákon musí být až za začátkem aktuálního okruhu
        for m in zakony:
            if okruh_pos <= m.start() <= pos:
                zakon_name = m.group(0).strip()
        return okruh_num, okruh_name, zakon_name

    # Parsování otázek
    q_pattern = re.compile(
        r'^(\d+)\.\s+(.+?)\n'          # číslo + text otázky
        r'a\)\s+(.+?)\n'               # odpověď a)
        r'b\)\s+(.+?)\n'               # odpověď b)
        r'c\)\s+(.+?)(?=\n\d+\.\s|\Z)',# odpověď c)
        re.MULTILINE | re.DOTALL
    )

    questions = []
    for m in q_pattern.finditer(text):
        num    = int(m.group(1))
        q_text = ' '.join(m.group(2).split())
        a      = ' '.join(m.group(3).split())
        b      = ' '.join(m.group(4).split())
        c_text = ' '.join(m.group(5).split())

        okruh_num, okruh_name, zakon = get_context(m.start())

        questions.append({
            "id":         num,
            "okruh_num":  okruh_num,
            "okruh":      okruh_name,
            "zakon":      zakon,
            "question":   q_text,
            "options": {
                "a": a,
                "b": b,
                "c": c_text
            }
        })

    return sorted(questions, key=lambda x: x["id"])

# ─── Získání správných odpovědí přes Anthropic API ────────────────────────────
def call_api(messages: list, system: str = "") -> str:
    """Zavolá Anthropic API a vrátí text odpovědi."""
    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1000,
        "messages": messages
    }
    if system:
        payload["system"] = system

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        raise RuntimeError(f"API chyba {e.code}: {body}")

def get_answers_from_api(questions: list[dict]) -> dict:
    """Získá správné odpovědi pro všechny otázky přes API v dávkách."""
    answers = {}
    total = len(questions)

    system_prompt = """Jsi expert na české právo a veřejnou správu.
Dostaneš seznam otázek z obecné části úřednické zkoušky státní služby ČR.
Každá otázka má 3 možnosti odpovědí (a, b, c) — právě jedna je správná.
Odpověz POUZE platným JSON objektem ve formátu:
{"1": "c", "2": "a", "15": "b", ...}
kde klíč je číslo otázky (string) a hodnota je písmeno správné odpovědi (a/b/c).
Žádný jiný text, žádné markdown bloky, pouze čistý JSON."""

    print(f"\n{c(C.CYAN, '📥 Stahuji správné odpovědi přes API...')}")
    print(f"Celkem {total} otázek v {(total + BATCH_SIZE - 1) // BATCH_SIZE} dávkách.\n")

    for batch_start in range(0, total, BATCH_SIZE):
        batch = questions[batch_start:batch_start + BATCH_SIZE]
        batch_end = min(batch_start + BATCH_SIZE, total)

        # Sestavení textu dávky
        prompt_lines = []
        for q in batch:
            prompt_lines.append(f"Otázka {q['id']} ({q['zakon'][:50] if q['zakon'] else q['okruh'][:50]}):")
            prompt_lines.append(f"  {q['question']}")
            for letter, text in q['options'].items():
                prompt_lines.append(f"  {letter}) {text}")
            prompt_lines.append("")

        user_msg = "Urči správnou odpověď pro každou otázku:\n\n" + "\n".join(prompt_lines)
        user_msg += "\n\nOdpověz pouze JSON objektem {\"číslo_otázky\": \"písmeno\", ...}."

        print(f"  Dávka {batch_start // BATCH_SIZE + 1}: otázky {batch[0]['id']}–{batch[-1]['id']}... ", end="", flush=True)

        try:
            response = call_api([{"role": "user", "content": user_msg}], system_prompt)

            # Extrakce JSON z odpovědi
            json_match = re.search(r'\{[^{}]+\}', response, re.DOTALL)
            if not json_match:
                print(c(C.RED, f"⚠ JSON nenalezen v odpovědi: {response[:100]}"))
                continue

            batch_answers = json.loads(json_match.group())
            answers.update({str(k): v.lower().strip() for k, v in batch_answers.items()})
            found = len([q for q in batch if str(q['id']) in answers])
            print(c(C.GREEN, f"✓ ({found}/{len(batch)} odpovědí)"))

        except Exception as e:
            print(c(C.RED, f"✗ Chyba: {e}"))

        time.sleep(0.5)

    return answers

# ─── Správa průběhu (progress) ────────────────────────────────────────────────
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}

def save_progress(progress: dict):
    PROGRESS_FILE.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")

def update_progress(progress: dict, q_id: int, correct: bool):
    key = str(q_id)
    if key not in progress:
        progress[key] = {"attempts": 0, "correct": 0, "last_seen": ""}
    progress[key]["attempts"] += 1
    if correct:
        progress[key]["correct"] += 1
    progress[key]["last_seen"] = datetime.now().strftime("%Y-%m-%d %H:%M")

def get_success_rate(progress: dict, q_id: int) -> float:
    key = str(q_id)
    if key not in progress or progress[key]["attempts"] == 0:
        return -1.0  # Nikdy nezkoušeno
    return progress[key]["correct"] / progress[key]["attempts"]

# ─── Zobrazení statistik ──────────────────────────────────────────────────────
def show_stats(questions: list[dict], answers: dict, progress: dict):
    print(f"\n{c(C.BOLD + C.CYAN, '═══ STATISTIKY ═══')}")
    total = len(questions)
    attempted = sum(1 for q in questions if str(q['id']) in progress and progress[str(q['id'])]['attempts'] > 0)
    never = total - attempted

    # Celkové skóre
    total_attempts = sum(p['attempts'] for p in progress.values())
    total_correct  = sum(p['correct']  for p in progress.values())
    overall_rate   = (total_correct / total_attempts * 100) if total_attempts > 0 else 0

    print(f"\n{c(C.BOLD, 'Celkový přehled:')}")
    print(f"  Otázky celkem:    {total}")
    print(f"  Již zkoušeno:     {attempted} ({attempted/total*100:.0f}%)")
    print(f"  Nikdy nezkoušeno: {never}")
    print(f"  Celková úspěšnost: {c(C.GREEN if overall_rate >= 75 else C.YELLOW if overall_rate >= 50 else C.RED, f'{overall_rate:.1f}%')}")

    # Statistiky per okruh
    okruhy = {}
    for q in questions:
        o = q['okruh_num']
        if o not in okruhy:
            okruhy[o] = {"name": q['okruh'], "total": 0, "attempts": 0, "correct": 0}
        okruhy[o]["total"] += 1
        key = str(q['id'])
        if key in progress:
            okruhy[o]["attempts"] += progress[key]["attempts"]
            okruhy[o]["correct"]  += progress[key]["correct"]

    print(f"\n{c(C.BOLD, 'Podle okruhů:')}")
    for num, data in sorted(okruhy.items()):
        rate = (data['correct'] / data['attempts'] * 100) if data['attempts'] > 0 else 0
        bar_filled = int(rate / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        rate_color = C.GREEN if rate >= 75 else C.YELLOW if rate >= 50 else C.RED
        print(f"  Okruh {num}: {c(C.BOLD, data['name'][:45])}")
        print(f"          {c(rate_color, bar)} {rate:.0f}% ({data['attempts']} pokusů)")

    # Nejtěžší otázky
    weak = []
    for q in questions:
        key = str(q['id'])
        if key in progress and progress[key]['attempts'] >= 2:
            rate = progress[key]['correct'] / progress[key]['attempts']
            if rate < 0.6:
                weak.append((rate, q))
    weak.sort(key=lambda x: x[0])

    if weak:
        print(f"\n{c(C.BOLD + C.RED, f'Nejslabší otázky ({len(weak)} s < 60% úspěšností):')}")
        for rate, q in weak[:10]:
            print(f"  [{rate*100:.0f}%] Ot. {q['id']}: {q['question'][:70]}...")

    print()

# ─── Kvíz ─────────────────────────────────────────────────────────────────────
def run_quiz(questions: list[dict], answers: dict, progress: dict,
             okruh: int = None, only_weak: bool = False, count: int = None):
    """Spustí interaktivní kvíz."""

    # Filtrování otázek
    pool = [q for q in questions if str(q['id']) in answers]

    if okruh:
        pool = [q for q in pool if q['okruh_num'] == okruh]

    if only_weak:
        pool = [q for q in pool if get_success_rate(progress, q['id']) < 0.6]
        if not pool:
            print(c(C.GREEN, "\n✓ Žádné slabé otázky! Všechny zvládáš na ≥60%."))
            return

    # Seřazení: nezkoušené nejdřív, pak podle úspěšnosti (nejslabší napřed)
    pool.sort(key=lambda q: (
        0 if get_success_rate(progress, q['id']) == -1 else 1,
        get_success_rate(progress, q['id'])
    ))

    if count:
        pool = pool[:count]

    random.shuffle(pool[:min(len(pool), 50)])  # Promíchej první várku

    if not pool:
        print(c(C.RED, "\nŽádné otázky k dispozici pro zadaná kritéria."))
        return

    # Záhlaví
    print(f"\n{c(C.BOLD + C.CYAN, '╔══════════════════════════════════════╗')}")
    print(f"{c(C.BOLD + C.CYAN, '║  PŘÍPRAVA NA ÚŘEDNICKOU ZKOUŠKU      ║')}")
    print(f"{c(C.BOLD + C.CYAN, '╚══════════════════════════════════════╝')}")
    if okruh:
        print(f"Okruh: {c(C.BOLD, pool[0]['okruh'] if pool else '')}")
    print(f"Otázek k procvičení: {c(C.BOLD, str(len(pool)))}")
    print(f"{c(C.GRAY, 'Zadej písmeno odpovědi (a/b/c) nebo q pro ukončení.')}\n")

    session_correct = 0
    session_total   = 0

    for idx, q in enumerate(pool):
        correct_letter = answers.get(str(q['id']), '?')
        rate = get_success_rate(progress, q['id'])

        # Progress bar session
        bar_done = "■" * session_correct + "□" * (session_total - session_correct)
        print(f"{c(C.GRAY, f'[{idx+1}/{len(pool)}]')} "
              f"{c(C.GRAY, f'Skóre: {session_correct}/{session_total}')} "
              f"{c(C.GRAY, bar_done[:20])}")

        # Celková history
        if rate >= 0:
            hist_color = C.GREEN if rate >= 0.75 else C.YELLOW if rate >= 0.5 else C.RED
            key = str(q['id'])
            att = progress[key]['attempts']
            cor = progress[key]['correct']
            print(c(C.GRAY, f"  Tvoje history: {cor}/{att} ({rate*100:.0f}%)"))

        # Zákon/zdroj
        if q['zakon']:
            print(c(C.GRAY, f"  ⚖ {q['zakon'][:80]}"))

        # Otázka
        q_num = q['id']
        print(f"\n{c(C.BOLD, f'Otázka {q_num}.')}")
        print(f"{c(C.BOLD, q['question'])}\n")

        # Možnosti
        for letter in ['a', 'b', 'c']:
            print(f"  {c(C.CYAN, letter + ')')} {q['options'][letter]}")

        # Vstup
        while True:
            try:
                answer = input(f"\n{c(C.YELLOW, '➤ Tvoje odpověď: ')}").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print(f"\n\n{c(C.YELLOW, 'Kvíz přerušen.')}")
                _show_session_result(session_correct, session_total)
                save_progress(progress)
                return

            if answer == 'q':
                print(f"\n{c(C.YELLOW, 'Kvíz ukončen.')}")
                _show_session_result(session_correct, session_total)
                save_progress(progress)
                return

            if answer in ['a', 'b', 'c']:
                break
            print(c(C.RED, "  Zadej a, b nebo c (nebo q pro ukončení)."))

        # Vyhodnocení
        session_total += 1
        is_correct = (answer == correct_letter)

        if is_correct:
            session_correct += 1
            print(f"\n{c(C.BG_GREEN + C.BOLD, '  ✓ SPRÁVNĚ!  ')}")
        else:
            print(f"\n{c(C.BG_RED + C.BOLD, '  ✗ ŠPATNĚ  ')}")
            print(f"  Správná odpověď: {c(C.GREEN + C.BOLD, correct_letter + ') ' + q['options'][correct_letter])}")

        update_progress(progress, q['id'], is_correct)
        save_progress(progress)
        print()

        # Pauza mezi otázkami
        input(c(C.GRAY, "  [Enter pro další otázku]"))
        print("\n" + "─" * 50 + "\n")

    # Závěrečné skóre
    _show_session_result(session_correct, session_total)

def _show_session_result(correct: int, total: int):
    if total == 0:
        return
    rate = correct / total * 100
    print(f"\n{c(C.BOLD + C.CYAN, '═══ VÝSLEDEK SEZENÍ ═══')}")
    print(f"Správně: {c(C.GREEN + C.BOLD, str(correct))}/{total}  ({rate:.1f}%)")
    if rate >= 80:
        print(c(C.GREEN, "🏆 Výborně! Jsi dobře připraven/a."))
    elif rate >= 60:
        print(c(C.YELLOW, "📚 Dobrý výsledek, ale je co zlepšovat."))
    else:
        print(c(C.RED, "💪 Trénuj dál — opakování je základ!"))
    print()

# ─── Hlavní funkce ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Kvíz pro přípravu na obecnou část úřednické zkoušky."
    )
    parser.add_argument("--get-answers", action="store_true",
                        help="Stáhni správné odpovědi přes API (spusť jednou)")
    parser.add_argument("--stats", action="store_true",
                        help="Zobraz statistiky pokroku")
    parser.add_argument("--okruh", type=int, choices=[1, 2, 3, 4],
                        help="Procvičuj pouze otázky z daného okruhu")
    parser.add_argument("--slabe", action="store_true",
                        help="Procvičuj pouze otázky s úspěšností < 60%%")
    parser.add_argument("--pocet", type=int,
                        help="Počet otázek v jednom sezení")
    args = parser.parse_args()

    # Načtení otázek
    if not QUESTIONS_PDF.exists():
        print(c(C.RED, f"Chybí soubor s otázkami: {QUESTIONS_PDF}"))
        print("Spusť skript ze správného adresáře, kde leží 'zkusebni_otazky.txt'.")
        sys.exit(1)

    raw_text = QUESTIONS_PDF.read_text(encoding="utf-8")
    questions = parse_questions(raw_text)

    if not questions:
        print(c(C.RED, "Nepodařilo se parsovat otázky ze souboru."))
        sys.exit(1)

    print(c(C.GRAY, f"Načteno {len(questions)} otázek."))

    # Akce: stažení odpovědí
    if args.get_answers:
        answers = {}
        if ANSWERS_FILE.exists():
            answers = json.loads(ANSWERS_FILE.read_text(encoding="utf-8"))
            missing = [q for q in questions if str(q['id']) not in answers]
            if not missing:
                print(c(C.GREEN, f"✓ Všechny odpovědi již máš uloženy ({len(answers)} ks)."))
                sys.exit(0)
            print(f"Chybí odpovědi pro {len(missing)} otázek, doplňuji...")
            new_answers = get_answers_from_api(missing)
        else:
            new_answers = get_answers_from_api(questions)

        answers.update(new_answers)
        ANSWERS_FILE.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
        print(c(C.GREEN, f"\n✓ Uloženo {len(answers)} odpovědí do {ANSWERS_FILE}"))
        sys.exit(0)

    # Načtení odpovědí
    if not ANSWERS_FILE.exists():
        print(c(C.YELLOW, "⚠ Odpovědi nejsou staženy. Spusť nejdřív:"))
        print(c(C.BOLD, "  python quiz.py --get-answers"))
        sys.exit(1)

    answers = json.loads(ANSWERS_FILE.read_text(encoding="utf-8"))
    progress = load_progress()

    covered = sum(1 for q in questions if str(q['id']) in answers)
    print(c(C.GRAY, f"Odpovědi k dispozici: {covered}/{len(questions)} otázek."))

    # Akce: statistiky
    if args.stats:
        show_stats(questions, answers, progress)
        sys.exit(0)

    # Kvíz
    run_quiz(
        questions=questions,
        answers=answers,
        progress=progress,
        okruh=args.okruh,
        only_weak=args.slabe,
        count=args.pocet
    )

if __name__ == "__main__":
    main()
