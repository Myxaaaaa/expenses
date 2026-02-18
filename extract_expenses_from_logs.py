import json
import re
from pathlib import Path


def extract_from_file(path: Path):
    text = path.read_text(encoding="utf-8")
    logs = json.loads(text)

    expenses = []
    pat = re.compile(r"Распарсен расход: ([0-9.,]+) - (.*)")
    pat2 = re.compile(r"Расход добавлен в БД с ID: (\\d+)")

    for item in logs:
        msg = item.get("message", "")
        m = pat.search(msg)
        if m:
            amount = m.group(1).replace(",", ".")
            desc = m.group(2)
            ts = item.get("timestamp")
            expenses.append((ts, amount, desc))
        elif pat2.search(msg):
            ts = item.get("timestamp")
            expenses.append((ts, None, "(есть запись, детали не найдены в логах)"))

    return expenses


def main() -> None:
    base = Path(".")
    log_files = sorted(base.glob("logs*.json"))
    if not log_files:
        print("Нет файлов logs*.json")
        return

    all_expenses = []
    for f in log_files:
        try:
            all_expenses.extend(extract_from_file(f))
        except Exception as e:
            print(f"Ошибка при разборе {f.name}: {e}")

    all_expenses.sort(key=lambda x: (x[0] or ""))

    lines = []
    for ts, amount, desc in all_expenses:
        if amount is None:
            lines.append(f"{ts or ''} | {desc}")
        else:
            lines.append(f"{ts or ''} | {amount} | {desc}")

    out_path = base / "all_expenses_from_logs.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Готово: {out_path} ({len(lines)} строк)")


if __name__ == "__main__":
    main()

