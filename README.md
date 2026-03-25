# Kvíz – Obecná část úřednické zkoušky

Interaktivní příkazový řádek pro přípravu na zkoušku státní služby ČR.

## Soubory
- `quiz.py` — hlavní kvíz (spouštěcí skript)
- `zkusebni_otazky.txt` — 300 otázek (extrahováno z PDF)
- `answers.json` — správné odpovědi (300 otázek)
- `progress.json` — tvůj pokrok (vytvoří se automaticky)
- `generate_answers.py` — přegenerování odpovědí přes vlastní API klíč

## Spuštění
```bash
# Normální kvíz (všechny otázky, prioritně nezkoušené a slabé)
python quiz.py

# Jen konkrétní okruh
python quiz.py --okruh 1    # Organizace a činnost veřejné správy
python quiz.py --okruh 2    # Práva, povinnosti a etika
python quiz.py --okruh 3    # Právní předpisy
python quiz.py --okruh 4    # Právo EU

# Jen otázky kde máš < 60% úspěšnost
python quiz.py --slabe

# Omezit počet otázek v jednom sezení
python quiz.py --pocet 20

# Kombinace
python quiz.py --okruh 3 --pocet 30

# Statistiky pokroku
python quiz.py --stats
```

## Přegenerování odpovědí (volitelné)
Pokud chceš odpovědi ověřit/přegenerovat přes Anthropic API:
```bash
export ANTHROPIC_API_KEY=sk-ant-xxxxx
python generate_answers.py
```

## Okruhy (300 otázek)
| # | Název | Počet otázek |
|---|-------|-------------|
| 1 | Organizace a činnost veřejné správy | 110 |
| 2 | Práva, povinnosti a etická pravidla státních zaměstnanců | 15 |
| 3 | Právní předpisy obecně dopadající na činnost státní správy | 150 |
| 4 | Právo Evropské unie | 25 |

## Průběh studia
Skript sleduje tvůj pokrok v `progress.json`:
- Kolikrát jsi každou otázku zkoušela
- Úspěšnost per otázka
- `--slabe` ti zobrazí jen to, co tě trápí

Doporučení: dělej 20–30 otázek denně, pravidelně přidávej `--slabe`.
