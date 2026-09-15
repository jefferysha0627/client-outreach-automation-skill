#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from email.message import EmailMessage
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET


BASE_DIR = Path(__file__).resolve().parent

CANONICAL_FIELDS = [
    "店家名稱",
    "Email",
    "聯絡人",
    "地址",
    "電話",
    "來源網址",
    "備註",
    "狀態",
]

TEMPLATE_FIELDS = [
    "店家名稱",
    "聯絡人",
    "地址",
    "電話",
    "來源網址",
    "備註",
    "學校名稱",
    "寄件人姓名",
    "寄件人身分",
    "寄件人Email",
    "寄件人LINE",
    "服務名稱",
]

ACTIVE_CONFIG: dict[str, Any] = {}

ALIASES = {
    "店家名稱": [
        "店家名稱",
        "店名",
        "商家名稱",
        "商店名稱",
        "名稱",
        "公司名稱",
        "品牌名稱",
        "店鋪名稱",
        "店家",
        "business name",
        "business",
        "name",
    ],
    "Email": [
        "email",
        "e-mail",
        "email address",
        "mail",
        "gmail",
        "信箱",
        "電子郵件",
        "電子信箱",
        "聯絡信箱",
        "客服信箱",
    ],
    "聯絡人": [
        "聯絡人",
        "負責人",
        "窗口",
        "稱呼",
        "姓名",
        "contact",
        "contact person",
    ],
    "地址": ["地址", "店址", "所在地", "地點", "address"],
    "電話": ["電話", "手機", "聯絡電話", "phone", "tel", "mobile"],
    "來源網址": [
        "來源網址",
        "資料來源",
        "來源",
        "source",
        "source url",
        "google maps 連結",
        "google maps",
        "官方網站",
        "網站",
        "網址",
        "url",
    ],
    "備註": [
        "備註",
        "說明",
        "note",
        "notes",
        "適合開發原因",
        "建議開場白",
        "網站狀況",
    ],
    "狀態": ["狀態", "聯絡狀態", "重複狀態", "status"],
}

ALIAS_TO_FIELD: dict[str, str] = {}


def normalize_header(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ｅｍａｉｌ", "email")
    return re.sub(r"[\s_\-:：/\\|,，.。()（）\[\]【】「」'\"　]+", "", text)


for canonical, aliases in ALIASES.items():
    for alias in aliases:
        ALIAS_TO_FIELD[normalize_header(alias)] = canonical


EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.IGNORECASE)
EMAIL_EXTRACT_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)

TERMINAL_STATUSES = {
    "已建立草稿",
    "已送出",
    "Email 空白",
    "Email 格式錯誤",
    "重複 Email",
    "已退信 Email",
    "已在歷史紀錄中",
    "狀態已標示略過",
}

SENT_FIELDNAMES = [
    "sent_at",
    "draft_id",
    "message_id",
    "thread_id",
    "email",
    "店家名稱",
    "subject",
    "status",
    "error",
]

BOUNCED_EMAIL_FIELDNAMES = [
    "added_at",
    "email",
    "reason",
    "source",
]


def load_config(config_path: Path) -> dict[str, Any]:
    global ACTIVE_CONFIG

    if not config_path.exists():
        raise SystemExit(f"找不到設定檔：{config_path}")

    with config_path.open("r", encoding="utf-8") as fh:
        config = json.load(fh)

    if config.get("mode", "draft") != "draft":
        raise SystemExit("安全限制：目前只支援 mode=draft，不支援直接寄出 Email。")

    config["_config_dir"] = str(config_path.resolve().parent)
    config["batch_size"] = int(config.get("batch_size", 20))
    config["daily_limit"] = int(config.get("daily_limit", 50))
    if config["batch_size"] <= 0 or config["daily_limit"] <= 0:
        raise SystemExit("batch_size 與 daily_limit 必須是正整數。")
    ACTIVE_CONFIG = config
    return config


def resolve_path(config: dict[str, Any], key: str) -> Path:
    raw = Path(config[key])
    if raw.is_absolute():
        return raw
    return (Path(config["_config_dir"]) / raw).resolve()


def bounced_email_file(config: dict[str, Any]) -> Path:
    raw = Path(str(config.get("bounced_email_file", "./bounced-emails.csv")))
    if raw.is_absolute():
        return raw
    return (Path(config["_config_dir"]) / raw).resolve()


def load_env_file(config: dict[str, Any]) -> Path | None:
    raw_env_file = config.get("env_file", "./.env")
    env_file = Path(raw_env_file)
    if not env_file.is_absolute():
        env_file = (Path(config["_config_dir"]) / env_file).resolve()
    if not env_file.exists():
        return None

    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and key not in os.environ:
            os.environ[key] = value
    return env_file


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone()


def timestamp() -> str:
    return now_local().isoformat(timespec="seconds")


def log_line(config: dict[str, Any], message: str) -> None:
    log_file = resolve_path(config, "log_file")
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp()}] {message}\n")


def canonical_for_header(header: Any) -> str | None:
    return ALIAS_TO_FIELD.get(normalize_header(header))


def header_score(row: list[Any]) -> tuple[int, set[str]]:
    fields = {field for cell in row if (field := canonical_for_header(cell))}
    score = len(fields) * 10
    if "Email" in fields:
        score += 30
    if "店家名稱" in fields:
        score += 10
    return score, fields


def find_header_row(rows: list[list[Any]]) -> tuple[int | None, int, set[str]]:
    best_index: int | None = None
    best_score = 0
    best_fields: set[str] = set()

    for index, row in enumerate(rows[:30]):
        score, fields = header_score(row)
        if score > best_score:
            best_index = index
            best_score = score
            best_fields = fields

    if best_index is None or best_score < 40 or "Email" not in best_fields:
        return None, best_score, best_fields
    return best_index, best_score, best_fields


def clean_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0") and re.fullmatch(r"\d+\.0", text):
        return text[:-2]
    return text


def rows_to_records(rows: list[list[Any]], source_sheet: str) -> tuple[list[dict[str, Any]], int | None, int]:
    header_index, score, _fields = find_header_row(rows)
    if header_index is None:
        return [], None, score

    headers = rows[header_index]
    records: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(clean_cell(cell) for cell in row):
            continue

        record: dict[str, Any] = {field: "" for field in CANONICAL_FIELDS}
        raw: dict[str, str] = {}

        for index, header in enumerate(headers):
            value = clean_cell(row[index]) if index < len(row) else ""
            header_text = clean_cell(header) or f"欄位{index + 1}"
            raw[header_text] = value

            field = canonical_for_header(header)
            if not field or not value:
                continue

            existing = record.get(field, "")
            if not existing:
                record[field] = value
            elif value not in existing.split(" / "):
                record[field] = f"{existing} / {value}"

        if any(record.get(field) for field in CANONICAL_FIELDS):
            record["_source_sheet"] = source_sheet
            record["_source_row"] = row_number
            record["_raw"] = raw
            records.append(record)

    return records, header_index, score


def read_csv_rows(path: Path) -> list[list[str]]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp950", "big5"):
        try:
            text = path.read_text(encoding=encoding)
            return list(csv.reader(text.splitlines()))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise SystemExit(f"無法讀取 CSV 編碼：{path} ({last_error})")


def read_csv_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records, _header_index, _score = rows_to_records(read_csv_rows(path), path.name)
    return records, [path.name] if records else []


def xml_texts(element: ET.Element) -> str:
    texts: list[str] = []
    for child in element.iter():
        if child.tag.endswith("}t") or child.tag == "t":
            texts.append(child.text or "")
    return "".join(texts)


def read_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        data = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []

    root = ET.fromstring(data)
    return [xml_texts(si) for si in root]


def resolve_xlsx_target(target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    if target.startswith("xl/"):
        return target
    return f"xl/{target}"


def read_workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rels: dict[str, str] = {}
    for rel in rels_root:
        rel_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rel_id and target:
            rels[rel_id] = resolve_xlsx_target(target)

    workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sheets: list[tuple[str, str]] = []
    for sheet in workbook_root.iter():
        if not (sheet.tag.endswith("}sheet") or sheet.tag == "sheet"):
            continue
        name = sheet.attrib.get("name", "Sheet")
        rel_id = sheet.attrib.get(rel_ns)
        if rel_id and rel_id in rels:
            sheets.append((name, rels[rel_id]))
    return sheets


def column_index(cell_ref: str) -> int:
    letters_match = re.match(r"[A-Z]+", cell_ref.upper())
    if not letters_match:
        return 0
    value = 0
    for char in letters_match.group(0):
        value = value * 26 + (ord(char) - ord("A") + 1)
    return value - 1


def xlsx_cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        inline = next((child for child in cell if child.tag.endswith("}is") or child.tag == "is"), None)
        return xml_texts(inline) if inline is not None else ""

    value_element = next((child for child in cell if child.tag.endswith("}v") or child.tag == "v"), None)
    if value_element is None or value_element.text is None:
        return ""

    raw_value = value_element.text
    if cell_type == "s":
        try:
            return shared_strings[int(raw_value)]
        except (ValueError, IndexError):
            return raw_value
    if cell_type == "b":
        return "TRUE" if raw_value == "1" else "FALSE"
    return raw_value


def read_xlsx_sheet_rows(zf: zipfile.ZipFile, sheet_path: str, shared_strings: list[str]) -> list[list[str]]:
    root = ET.fromstring(zf.read(sheet_path))
    rows: list[list[str]] = []

    for row_element in root.iter():
        if not (row_element.tag.endswith("}row") or row_element.tag == "row"):
            continue

        values: dict[int, str] = {}
        max_col = -1
        current_col = 0
        for cell in row_element:
            if not (cell.tag.endswith("}c") or cell.tag == "c"):
                continue
            ref = cell.attrib.get("r", "")
            col = column_index(ref) if ref else current_col
            values[col] = clean_cell(xlsx_cell_value(cell, shared_strings))
            max_col = max(max_col, col)
            current_col = col + 1

        if max_col >= 0:
            rows.append([values.get(index, "") for index in range(max_col + 1)])

    return rows


def excluded_sheet_name(name: str) -> bool:
    normalized = normalize_header(name)
    excluded_terms = ["已存在", "疑似", "重複", "統計", "summary", "existing", "duplicate"]
    return any(term in normalized for term in excluded_terms)


def read_xlsx_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    with zipfile.ZipFile(path) as zf:
        shared_strings = read_shared_strings(zf)
        candidates: list[dict[str, Any]] = []
        for sheet_name, sheet_path in read_workbook_sheets(zf):
            rows = read_xlsx_sheet_rows(zf, sheet_path, shared_strings)
            records, header_index, score = rows_to_records(rows, sheet_name)
            if header_index is None:
                continue
            candidates.append(
                {
                    "sheet_name": sheet_name,
                    "records": records,
                    "score": score,
                    "excluded": excluded_sheet_name(sheet_name),
                }
            )

    selected = [candidate for candidate in candidates if not candidate["excluded"]]
    if not selected:
        selected = candidates

    all_records: list[dict[str, Any]] = []
    sheet_names: list[str] = []
    for candidate in selected:
        all_records.extend(candidate["records"])
        sheet_names.append(candidate["sheet_name"])

    return all_records, sheet_names


def read_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return read_csv_records(path)
    if suffix == ".xlsx":
        return read_xlsx_records(path)
    raise SystemExit(f"不支援的檔案格式：{path}")


def source_signature(path: Path) -> dict[str, str]:
    stat = path.stat()
    return {
        "source_path": str(path.resolve()),
        "source_size": str(stat.st_size),
        "source_mtime_ns": str(stat.st_mtime_ns),
    }


def read_processed_file_keys(processed_file: Path) -> set[tuple[str, str, str]]:
    if not processed_file.exists():
        return set()

    keys: set[tuple[str, str, str]] = set()
    with processed_file.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            keys.add((row.get("source_path", ""), row.get("source_size", ""), row.get("source_mtime_ns", "")))
    return keys


def discover_source_file(config: dict[str, Any], explicit_source: str | None) -> Path | None:
    if explicit_source:
        source = Path(explicit_source)
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        if not source.exists():
            raise SystemExit(f"找不到指定來源檔：{source}")
        return source

    output_folder = resolve_path(config, "output_folder")
    output_folder.mkdir(parents=True, exist_ok=True)
    processed_file = resolve_path(config, "processed_files_file")
    processed_keys = read_processed_file_keys(processed_file)
    recursive = bool(config.get("scan_recursive", True))
    iterator = output_folder.rglob("*") if recursive else output_folder.glob("*")
    candidates: list[Path] = []

    for path in iterator:
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name.startswith("~$"):
            continue
        if path.suffix.lower() not in {".xlsx", ".csv"}:
            continue
        if "_processed" in path.stem:
            continue
        signature = source_signature(path)
        key = (signature["source_path"], signature["source_size"], signature["source_mtime_ns"])
        if key in processed_keys:
            continue
        candidates.append(path)

    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.stat().st_mtime)


def extract_email(value: Any) -> tuple[str, str | None]:
    text = str(value or "").strip()
    if not text:
        return "", "Email 空白"

    text = text.replace("mailto:", "")
    match = EMAIL_EXTRACT_RE.search(text)
    candidate = match.group(0) if match else text
    candidate = candidate.strip().strip("<>,;").lower()

    if not EMAIL_RE.match(candidate):
        return candidate, "Email 格式錯誤"
    local, domain = candidate.rsplit("@", 1)
    if ".." in local or ".." in domain or domain.startswith("-") or domain.endswith("-"):
        return candidate, "Email 格式錯誤"
    return candidate, None


def load_history(history_file: Path, today: str) -> tuple[set[str], int]:
    if not history_file.exists():
        return set(), 0

    emails: set[str] = set()
    today_count = 0
    with history_file.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            email = (row.get("email") or row.get("Email") or "").strip().lower()
            if email:
                emails.add(email)
            if (row.get("created_at") or "").startswith(today):
                today_count += 1
    return emails, today_count


def load_bounced_emails(path: Path) -> set[str]:
    if not path.exists():
        return set()

    emails: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            raw_email = (row.get("email") or row.get("Email") or "").strip().lower()
            if EMAIL_RE.match(raw_email):
                emails.add(raw_email)
    return emails


def append_bounced_email(path: Path, email: str, reason: str, source: str) -> bool:
    normalized = email.strip().lower()
    if not EMAIL_RE.match(normalized):
        return False

    existing = load_bounced_emails(path)
    if normalized in existing:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=BOUNCED_EMAIL_FIELDNAMES)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "added_at": timestamp(),
                "email": normalized,
                "reason": reason,
                "source": source,
            }
        )
    return True


def parse_template(template_file: Path, subject_fallback: str) -> tuple[str, str]:
    if not template_file.exists():
        raise SystemExit(f"找不到信件模板：{template_file}")
    text = template_file.read_text(encoding="utf-8")
    lines = text.splitlines()

    if lines:
        first = lines[0].strip()
        if first.lower().startswith("subject:"):
            subject = first.split(":", 1)[1].strip()
            body = "\n".join(lines[1:]).lstrip("\n")
            return subject, body
        if first.startswith("主旨：") or first.startswith("主旨:"):
            subject = re.split(r"[:：]", first, maxsplit=1)[1].strip()
            body = "\n".join(lines[1:]).lstrip("\n")
            return subject, body

    return subject_fallback, text


def load_templates(config: dict[str, Any]) -> dict[str, tuple[str, str]]:
    templates: dict[str, tuple[str, str]] = {
        "zh": parse_template(resolve_path(config, "template_file"), str(config.get("subject_template") or "")),
    }
    for language, template_path in dict(config.get("template_files") or {}).items():
        raw_path = Path(str(template_path))
        path = raw_path if raw_path.is_absolute() else (Path(config["_config_dir"]) / raw_path).resolve()
        fallback = str(config.get("subject_template") or "")
        templates[str(language)] = parse_template(path, fallback)
    return templates


def render_text(template: str, record: dict[str, Any]) -> str:
    rendered = template
    for field in TEMPLATE_FIELDS:
        rendered = rendered.replace(f"{{{field}}}", str(record.get(field, "") or ""))
    return rendered


PERSONALIZATION_RULES: list[dict[str, Any]] = [
    {
        "keywords": ["vtuber", "v-tuber", ".vt", "直播", "實況", "虛擬", "shiro", "lingna", "蘿希"],
        "business": "Vtuber / 內容創作",
        "improvement": "角色介紹、作品連結、活動資訊和合作方式",
        "help": "作品集或個人形象頁",
        "note": "讓第一次認識你們的人，不用翻很多貼文也能快速找到代表作品和聯絡方式。",
    },
    {
        "keywords": ["音樂", "music", "guitar", "mixing", "樂團", "band", "orchestra", "con brio", "聲音", "錄音"],
        "business": "音樂 / 聲音創作",
        "improvement": "作品連結、服務介紹、合作方式和社群導流",
        "help": "作品集或服務介紹頁",
        "note": "音樂和聲音作品如果有一個整理頁，會更方便讓對方一次聽到重點作品。",
    },
    {
        "keywords": ["插畫", "illustration", "illustrator", "art", "artist", "繪", "畫", "設計", "design", "創作", "原創"],
        "business": "插畫 / 設計 / 創作作品",
        "improvement": "作品分類、委託說明、合作案例和社群導流",
        "help": "作品展示頁或創作者形象網站",
        "note": "把作品整理成比較好瀏覽的頁面，會比只靠社群貼文更容易讓新客戶理解風格。",
    },
    {
        "keywords": ["攝影", "影像", "photo", "photography", "film", "video", "一向影像"],
        "business": "攝影 / 影像服務",
        "improvement": "作品集、服務流程、方案說明和詢問入口",
        "help": "攝影作品集網站",
        "note": "影像作品如果有一個獨立頁面整理，客戶在詢問前會比較快看懂風格與服務內容。",
    },
    {
        "keywords": ["甜點", "烘焙", "餅乾", "cookie", "dango", "cake", "bake", "咖啡", "food", "foodie", "吃貨", "餐", "食", "茶", "飲"],
        "business": "餐飲 / 甜點 / 食品內容",
        "improvement": "品牌介紹、菜單或商品展示、訂購方式和社群導流",
        "help": "品牌形象頁或商品展示頁",
        "note": "如果把品項、品牌故事和下單方式集中起來，第一次看到的客人會比較容易決定是否詢問。",
    },
    {
        "keywords": ["美妝", "美甲", "nail", "beauty", "香氛", "保養", "皮膚", "妝", "髮"],
        "business": "美業 / 保養 / 香氛服務",
        "improvement": "服務介紹、價格或預約入口、案例照片和常見問題",
        "help": "美業形象頁或預約導流頁",
        "note": "美業類內容很吃信任感，網站可以把服務特色、案例與聯絡方式整理得更清楚。",
    },
    {
        "keywords": ["手作", "選品", "飾品", "皮革", "香氛", "蠟燭", "商品", "shop", "store", "studio"],
        "business": "手作 / 選品 / 商品品牌",
        "improvement": "商品分類、品牌故事、購買或詢問入口",
        "help": "商品展示頁或輕電商展示網站",
        "note": "商品如果能用網站整理分類和特色，客人會比在社群貼文中搜尋更容易理解。",
    },
    {
        "keywords": ["課程", "教室", "瑜伽", "yoga", "身心", "工作室", "教學", "諮詢"],
        "business": "課程 / 工作室 / 服務型品牌",
        "improvement": "服務項目、課程介紹、師資或品牌理念和預約入口",
        "help": "工作室形象網站",
        "note": "服務型品牌如果把課程與聯絡方式集中整理，能讓有興趣的人更快判斷適不適合。",
    },
    {
        "keywords": ["民宿", "旅宿", "hostel", "hotel", "住宿", "bnb", "旅行"],
        "business": "旅宿 / 空間品牌",
        "improvement": "空間介紹、房型資訊、交通位置和訂房導流",
        "help": "旅宿形象網站",
        "note": "旅宿類網站可以補足社群貼文不容易整理的房型、位置與預訂資訊。",
    },
]


def personalization_text(record: dict[str, Any]) -> str:
    raw_values = [str(record.get(field, "") or "") for field in CANONICAL_FIELDS]
    raw = " ".join(raw_values + [str(record.get("_source_sheet", "") or ""), str(record.get("_source_file", "") or "")])
    return raw.lower()


def infer_personalization(record: dict[str, Any]) -> dict[str, str]:
    text = personalization_text(record)
    matched = next(
        (
            rule
            for rule in PERSONALIZATION_RULES
            if any(str(keyword).lower() in text for keyword in rule["keywords"])
        ),
        None,
    )

    if matched is None:
        matched = {
            "business": "品牌或創作內容",
            "improvement": "品牌介紹、作品或商品內容、聯絡方式",
            "help": "簡單的形象網站或展示頁",
            "note": "把資訊集中到一個清楚的頁面，會比只靠社群貼文更容易讓第一次看到的人理解。",
        }

    has_source = bool(str(record.get("來源網址", "") or "").strip())
    has_note = bool(str(record.get("備註", "") or "").strip())
    business = str(matched["business"])
    opening_prefix = (
        "我有先看過名單裡提供的公開資訊"
        if has_source or has_note
        else "我先從店名和名單資料初步了解"
    )
    opening = f"{opening_prefix}，感覺你們的{business}蠻有特色。"

    return {
        "業務內容": business,
        "客製化開場": opening,
        "改善方向": str(matched["improvement"]),
        "可協助內容": str(matched["help"]),
        "客製化補充": str(matched["note"]),
    }


def apply_personalization(record: dict[str, Any]) -> None:
    if record.get("_personalized"):
        return
    for field, value in infer_personalization(record).items():
        record[field] = value
    record["_personalized"] = True


def record_text(record: dict[str, Any]) -> str:
    return " ".join(
        str(record.get(field, "") or "")
        for field in ("店家名稱", "地址", "來源網址", "備註", "_source_sheet", "_source_file")
    )


def is_taiwan_record(record: dict[str, Any]) -> bool:
    lowered = record_text(record).lower()
    taiwan_terms = [
        "taiwan",
        ".tw",
        "台灣",
        "臺灣",
        "台北",
        "臺北",
        "新北",
        "桃園",
        "新竹",
        "苗栗",
        "台中",
        "臺中",
        "彰化",
        "南投",
        "雲林",
        "嘉義",
        "台南",
        "臺南",
        "高雄",
        "屏東",
        "宜蘭",
        "花蓮",
        "台東",
        "臺東",
        "澎湖",
        "金門",
        "馬祖",
        "keelung",
        "taipei",
        "taoyuan",
        "hsinchu",
        "miaoli",
        "taichung",
        "changhua",
        "nantou",
        "yunlin",
        "chiayi",
        "tainan",
        "kaohsiung",
        "pingtung",
        "yilan",
        "hualien",
        "taitung",
        "penghu",
        "kinmen",
    ]
    return any(term in lowered for term in taiwan_terms)


def is_chinese_speaking_record(record: dict[str, Any]) -> bool:
    lowered = record_text(record).lower()
    chinese_speaking_terms = [
        "中文可溝通",
        "chinese_speaking",
        "chinese-speaking",
        "chinese speaking",
    ]
    return any(term in lowered for term in chinese_speaking_terms)


def is_non_taiwan_record(record: dict[str, Any]) -> bool:
    lowered = record_text(record).lower()
    non_taiwan_terms = [
        "香港",
        "hong kong",
        "澳門",
        "macau",
        "中國",
        "中国",
        "china",
        "新加坡",
        "singapore",
        "馬來西亞",
        "malaysia",
        "日本",
        "japan",
        ".jp",
        "韓國",
        "korea",
        ".kr",
        "美國",
        "united states",
        "usa",
        "canada",
        "uk",
        "australia",
        "thailand",
        "vietnam",
        "philippines",
        "indonesia",
    ]
    return any(term in lowered for term in non_taiwan_terms)


def school_name_for_record(record: dict[str, Any], language: str) -> str:
    school_names = dict(ACTIVE_CONFIG.get("school_names") or {})
    if language == "zh":
        key = "zh_non_taiwan" if is_non_taiwan_record(record) else "zh"
        return str(school_names.get(key) or school_names.get("zh") or "你的學校或機構名稱")
    if language == "ja":
        return str(school_names.get("ja") or school_names.get("en") or "your school or organization")
    if language == "ko":
        return str(school_names.get("ko") or school_names.get("en") or "your school or organization")
    return str(school_names.get("en") or school_names.get("zh") or "your school or organization")


def detect_template_language(record: dict[str, Any]) -> str:
    text = record_text(record)
    lowered = text.lower()
    japanese_terms = ["japan", "tokyo", "osaka", "kyoto", ".jp", "wagashi", "sushi", "ramen"]
    korean_terms = ["korea", "seoul", ".kr", "k-pop", "korean"]

    if is_taiwan_record(record) or is_chinese_speaking_record(record):
        return "zh"

    if re.search(r"[\u3040-\u30ff]", text):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", text):
        return "ko"
    if any(term in lowered for term in japanese_terms):
        return "ja"
    if any(term in lowered for term in korean_terms):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"

    return "en"


def template_for_record(
    templates: dict[str, tuple[str, str]],
    record: dict[str, Any],
) -> tuple[str, str, str]:
    language = detect_template_language(record)
    subject, body = templates.get(language) or templates.get("en") or templates["zh"]
    record["_template_language"] = language if language in templates else "zh"
    record["學校名稱"] = school_name_for_record(record, record["_template_language"])
    sender = dict(ACTIVE_CONFIG.get("sender") or {})
    record["寄件人姓名"] = str(sender.get("name") or "你的名字")
    record["寄件人身分"] = str(sender.get("title") or "你的身分 / 公司名稱")
    record["寄件人Email"] = str(sender.get("email") or "your-email@example.com")
    record["寄件人LINE"] = str(sender.get("line") or "")
    record["服務名稱"] = str(sender.get("service_name") or "網站建置服務")
    return subject, body, record["_template_language"]


class GmailDraftClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, user_id: str = "me"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.user_id = user_id or "me"
        self._access_token: str | None = None

    @classmethod
    def from_env(cls, config: dict[str, Any]) -> "GmailDraftClient":
        missing = [
            name
            for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
            if not os.environ.get(name)
        ]
        if missing:
            joined = ", ".join(missing)
            raise RuntimeError(
                "Gmail API 尚未設定，無法建立草稿。請設定環境變數："
                f"{joined}。Refresh token 需要包含 https://www.googleapis.com/auth/gmail.compose 權限。"
            )
        return cls(
            os.environ["GMAIL_CLIENT_ID"],
            os.environ["GMAIL_CLIENT_SECRET"],
            os.environ["GMAIL_REFRESH_TOKEN"],
            str(config.get("gmail_user_id") or "me"),
        )

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token

        payload = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gmail OAuth refresh token 換取 access token 失敗：{detail}") from exc

        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"Gmail OAuth 回應缺少 access_token：{data}")
        self._access_token = token
        return token

    def create_draft(self, to_email: str, subject: str, body: str) -> str:
        message = EmailMessage()
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        payload = json.dumps({"message": {"raw": raw}}).encode("utf-8")
        url = f"https://gmail.googleapis.com/gmail/v1/users/{urllib.parse.quote(self.user_id)}/drafts"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gmail 建立草稿失敗：{detail}") from exc

        draft_id = data.get("id")
        if not draft_id:
            raise RuntimeError(f"Gmail 建立草稿回應缺少 draft id：{data}")
        return draft_id

    def send_draft(self, draft_id: str) -> dict[str, Any]:
        payload = json.dumps({"id": draft_id}).encode("utf-8")
        url = f"https://gmail.googleapis.com/gmail/v1/users/{urllib.parse.quote(self.user_id)}/drafts/send"
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Gmail 送出草稿失敗：{detail}") from exc


def prepare_records(
    records: list[dict[str, Any]],
    history_emails: set[str],
    bounced_emails: set[str],
    config: dict[str, Any],
    allowed_to_create: int,
) -> tuple[list[dict[str, Any]], list[int]]:
    seen_emails: set[str] = set()
    eligible_indices: list[int] = []
    skip_status_values = {str(value).strip() for value in config.get("skip_status_values", [])}

    for index, record in enumerate(records):
        email, invalid_status = extract_email(record.get("Email", ""))
        record["_email_normalized"] = email
        record["_draft_id"] = ""
        record["_error"] = ""

        if invalid_status:
            record["_process_status"] = invalid_status
            continue

        if email in seen_emails:
            record["_process_status"] = "重複 Email"
            continue
        seen_emails.add(email)

        if email in bounced_emails:
            record["_process_status"] = "已退信 Email"
            continue

        if email in history_emails:
            record["_process_status"] = "已在歷史紀錄中"
            continue

        source_status = str(record.get("狀態", "") or "").strip()
        if source_status in skip_status_values:
            record["_process_status"] = "狀態已標示略過"
            continue

        eligible_indices.append(index)

    for position, index in enumerate(eligible_indices):
        if position < allowed_to_create:
            records[index]["_process_status"] = "待建立草稿"
        elif allowed_to_create <= 0:
            records[index]["_process_status"] = "未處理：已達每日上限"
        else:
            records[index]["_process_status"] = "未處理：超過本次批次上限"

    return records, [index for index in eligible_indices[:allowed_to_create]]


def summary_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        status = str(record.get("_process_status", "未處理"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def print_summary(
    source_file: Path,
    records: list[dict[str, Any]],
    to_create: list[int],
    daily_remaining: int,
    action_label: str = "建立草稿",
) -> None:
    print(f"來源檔案：{source_file}")
    print(f"讀取筆數：{len(records)}")
    print(f"今日剩餘可建立草稿數：{daily_remaining}")
    print("處理狀態：")
    for status, count in sorted(summary_counts(records).items()):
        print(f"  - {status}: {count}")

    if to_create:
        print(f"\n本次將{action_label}的前 10 筆：")
        for index in to_create[:10]:
            record = records[index]
            print(f"  - {record.get('店家名稱') or '(未命名)'} <{record.get('_email_normalized')}>")

    foreign_language_records = [
        record
        for record in records
        if record.get("_process_status")
        in {"已建立草稿", "已送出", "待建立草稿", "待建立草稿（dry run）", "待建立草稿（需確認）", "待送出（需確認）"}
        and record.get("_template_language")
        and record.get("_template_language") != "zh"
    ]
    if foreign_language_records:
        language_names = {"en": "英文", "ja": "日文", "ko": "韓文"}
        print("\n本次使用外語草稿的店家：")
        for record in foreign_language_records:
            language = language_names.get(str(record.get("_template_language")), str(record.get("_template_language")))
            print(f"  - {record.get('店家名稱') or '(未命名)'}：{language}")


def print_draft_previews(
    templates: dict[str, tuple[str, str]],
    records: list[dict[str, Any]],
    to_create: list[int],
    limit: int,
) -> None:
    if limit <= 0 or not to_create:
        return
    print(f"\n草稿預覽（前 {min(limit, len(to_create))} 封）：")
    for preview_number, index in enumerate(to_create[:limit], start=1):
        record = records[index]
        template_subject, template_body, _language = template_for_record(templates, record)
        subject = render_text(template_subject, record)
        body = render_text(template_body, record)
        print("\n" + "=" * 72)
        print(f"預覽 {preview_number}：{record.get('店家名稱') or '(未命名)'} <{record.get('_email_normalized')}>")
        print(f"Subject: {subject}")
        print("-" * 72)
        print(body)


def write_history(history_file: Path, rows: list[dict[str, str]]) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "created_at",
        "email",
        "店家名稱",
        "source_file",
        "source_sheet",
        "source_row",
        "draft_id",
        "subject",
    ]
    exists = history_file.exists()
    with history_file.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_sent_rows(sent_history_file: Path, rows: list[dict[str, str]]) -> None:
    sent_history_file.parent.mkdir(parents=True, exist_ok=True)
    exists = sent_history_file.exists()
    with sent_history_file.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SENT_FIELDNAMES)
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in SENT_FIELDNAMES})


def write_processed_report(
    processed_folder: Path,
    source_file: Path,
    records: list[dict[str, Any]],
) -> Path:
    processed_folder.mkdir(parents=True, exist_ok=True)
    run_id = now_local().strftime("%Y%m%d_%H%M%S")
    report_file = processed_folder / f"{source_file.stem}_processed_{run_id}.csv"
    fieldnames = [
        "processed_at",
        "source_file",
        "source_sheet",
        "source_row",
        "店家名稱",
        "Email",
        "聯絡人",
        "地址",
        "電話",
        "來源網址",
        "備註",
        "原始狀態",
        "處理狀態",
        "draft_id",
        "message_id",
        "thread_id",
        "錯誤訊息",
    ]

    with report_file.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "processed_at": timestamp(),
                    "source_file": str(source_file.resolve()),
                    "source_sheet": record.get("_source_sheet", ""),
                    "source_row": record.get("_source_row", ""),
                    "店家名稱": record.get("店家名稱", ""),
                    "Email": record.get("_email_normalized") or record.get("Email", ""),
                    "聯絡人": record.get("聯絡人", ""),
                    "地址": record.get("地址", ""),
                    "電話": record.get("電話", ""),
                    "來源網址": record.get("來源網址", ""),
                    "備註": record.get("備註", ""),
                    "原始狀態": record.get("狀態", ""),
                    "處理狀態": record.get("_process_status", ""),
                    "draft_id": record.get("_draft_id", ""),
                    "message_id": record.get("_message_id", ""),
                    "thread_id": record.get("_thread_id", ""),
                    "錯誤訊息": record.get("_error", ""),
                }
            )

    return report_file


def mark_source_processed(processed_file: Path, source_file: Path, report_file: Path, records: list[dict[str, Any]]) -> None:
    processed_file.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "processed_at",
        "source_path",
        "source_size",
        "source_mtime_ns",
        "processed_report",
        "total_rows",
        "successful_drafts",
        "successful_sent",
    ]
    exists = processed_file.exists()
    signature = source_signature(source_file)
    with processed_file.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(
            {
                "processed_at": timestamp(),
                **signature,
                "processed_report": str(report_file.resolve()),
                "total_rows": str(len(records)),
                "successful_drafts": str(sum(1 for record in records if record.get("_process_status") == "已建立草稿")),
                "successful_sent": str(sum(1 for record in records if record.get("_process_status") == "已送出")),
            }
        )


def all_terminal(records: list[dict[str, Any]]) -> bool:
    return all(str(record.get("_process_status", "")) in TERMINAL_STATUSES for record in records)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="將客戶名單轉成 Gmail 草稿。")
    parser.add_argument("--config", default=str(BASE_DIR / "config.json"), help="config.json 路徑")
    parser.add_argument("--source-file", help="指定要處理的 Excel / CSV；不指定時會掃描 output_folder")
    parser.add_argument("--confirm", action="store_true", help="在 require_review=true 時確認建立 Gmail 草稿")
    parser.add_argument("--send", "--direct-send", action="store_true", help="建立草稿後立即送出 Gmail；必須同時加 --confirm-send")
    parser.add_argument("--confirm-send", action="store_true", help="明確確認本次要直接送出 Gmail，不只建立草稿")
    parser.add_argument("--confirm-foreign-languages", action="store_true", help="確認本批外語模板判斷無誤並建立草稿")
    parser.add_argument("--dry-run", action="store_true", help="只預覽，不建立草稿、不寫入歷史、不標記來源檔")
    parser.add_argument("--preview-drafts", type=int, default=0, help="預覽前 N 封完整渲染草稿，不建立 Gmail 草稿")
    parser.add_argument("--check-setup", action="store_true", help="檢查設定、來源檔、模板與 Gmail OAuth")
    parser.add_argument("--add-bounced-email", help="加入已退信 Email 黑名單，不建立草稿、不寄信")
    parser.add_argument("--bounce-reason", default="退信：收件信箱不存在或無法接收", help="搭配 --add-bounced-email 使用的退信原因")
    parser.add_argument("--bounce-source", default="manual", help="搭配 --add-bounced-email 使用的來源註記")
    return parser


def check_setup(config: dict[str, Any], source_file_arg: str | None, env_file: Path | None) -> int:
    print("設定檢查：")
    print(f"  - mode: {config.get('mode')}")
    print(f"  - batch_size: {config.get('batch_size')}")
    print(f"  - daily_limit: {config.get('daily_limit')}")
    print(f"  - require_review: {config.get('require_review')}")

    output_folder = resolve_path(config, "output_folder")
    template_file = resolve_path(config, "template_file")
    history_file = resolve_path(config, "history_file")
    log_file = resolve_path(config, "log_file")
    processed_folder = resolve_path(config, "processed_folder")
    bounce_file = bounced_email_file(config)

    print("\n路徑檢查：")
    print(f"  - output_folder: {output_folder} ({'存在' if output_folder.exists() else '會自動建立'})")
    print(f"  - processed_folder: {processed_folder} ({'存在' if processed_folder.exists() else '會自動建立'})")
    print(f"  - template_file: {template_file} ({'存在' if template_file.exists() else '缺少'})")
    print(f"  - history_file: {history_file} ({'存在' if history_file.exists() else '尚未建立'})")
    print(f"  - bounced_email_file: {bounce_file} ({'存在' if bounce_file.exists() else '尚未建立'})")
    print(f"  - log_file: {log_file} ({'存在' if log_file.exists() else '尚未建立'})")
    print(f"  - env_file: {env_file or (Path(config['_config_dir']) / str(config.get('env_file', './.env')))} ({'已載入' if env_file else '缺少'})")

    source_file = discover_source_file(config, source_file_arg)
    print("\n來源名單檢查：")
    if source_file:
        print(f"  - 下一個待處理檔案：{source_file}")
        try:
            records, sheet_names = read_records(source_file)
            print(f"  - 可讀取資料筆數：{len(records)}")
            if sheet_names:
                print(f"  - 讀取工作表/來源：{', '.join(sheet_names)}")
        except Exception as exc:  # noqa: BLE001 - setup check should report the exact failure.
            print(f"  - 讀取失敗：{exc}")
            return 2
    else:
        print("  - 沒有找到尚未處理的新 Excel / CSV 檔案")

    print("\nGmail OAuth 檢查：")
    missing = [
        name
        for name in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN")
        if not os.environ.get(name)
    ]
    if missing:
        print(f"  - 缺少：{', '.join(missing)}")
        print("  - 請先執行：python3 client-outreach/setup-gmail-oauth.py")
        return 2

    try:
        GmailDraftClient.from_env(config).access_token()
    except Exception as exc:  # noqa: BLE001 - setup check should surface auth failures.
        print(f"  - 授權測試失敗：{exc}")
        return 2

    print("  - Gmail compose 授權可用")
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    env_file = load_env_file(config)

    if args.check_setup:
        return check_setup(config, args.source_file, env_file)

    if args.add_bounced_email:
        path = bounced_email_file(config)
        added = append_bounced_email(path, args.add_bounced_email, args.bounce_reason, args.bounce_source)
        status = "已加入" if added else "已存在或格式無效，未新增"
        print(f"{status}：{args.add_bounced_email.strip().lower()}")
        print(f"退信 Email 黑名單：{path}")
        return 0

    if args.confirm_send and not args.send:
        print("安全限制：--confirm-send 必須搭配 --send 使用。", file=sys.stderr)
        return 2

    source_file = discover_source_file(config, args.source_file)
    if source_file is None:
        message = "沒有找到尚未處理的新 Excel / CSV 檔案。"
        print(message)
        log_line(config, message)
        return 0

    templates = load_templates(config)

    records, sheet_names = read_records(source_file)
    for record in records:
        record["_source_file"] = str(source_file)
    today = now_local().date().isoformat()
    history_file = resolve_path(config, "history_file")
    history_emails, today_count = load_history(history_file, today)
    bounced_emails = load_bounced_emails(bounced_email_file(config))
    daily_remaining = max(0, int(config["daily_limit"]) - today_count)
    allowed_to_create = min(int(config["batch_size"]), daily_remaining)
    records, to_create = prepare_records(records, history_emails, bounced_emails, config, allowed_to_create)
    for index in to_create:
        _subject, _body, _language = template_for_record(templates, records[index])

    if args.dry_run:
        for index in to_create:
            records[index]["_process_status"] = "待建立草稿（dry run）"
        print_summary(source_file, records, to_create, daily_remaining)
        print_draft_previews(templates, records, to_create, args.preview_drafts)
        log_line(
            config,
            f"dry-run source={source_file} rows={len(records)} sheets={','.join(sheet_names)} to_create={len(to_create)}",
        )
        return 0

    if args.preview_drafts:
        for index in to_create:
            records[index]["_process_status"] = "待建立草稿（預覽）"
        print_summary(source_file, records, to_create, daily_remaining)
        print_draft_previews(templates, records, to_create, args.preview_drafts)
        log_line(
            config,
            f"preview-drafts source={source_file} rows={len(records)} sheets={','.join(sheet_names)} to_create={len(to_create)}",
        )
        return 0

    if args.send and not args.confirm_send:
        for index in to_create:
            records[index]["_process_status"] = "待送出（需確認）"
        print_summary(source_file, records, to_create, daily_remaining, "直接寄出")
        print("\n本次要求直接送出 Gmail，但尚未提供 --confirm-send，因此沒有建立草稿也沒有送出。")
        print("確認要直接寄出後請改用：")
        print(f"python3 {Path(__file__).resolve()} --confirm --send --confirm-send")
        log_line(
            config,
            f"send-confirm-required source={source_file} rows={len(records)} sheets={','.join(sheet_names)} to_send={len(to_create)}",
        )
        return 0

    if args.send and args.confirm_send and not args.confirm:
        for index in to_create:
            records[index]["_process_status"] = "待送出（需確認）"
        print_summary(source_file, records, to_create, daily_remaining, "直接寄出")
        print("\n直接寄出還需要 --confirm 才會通過批次確認；尚未建立草稿也沒有送出。")
        print("確認要直接寄出後請改用：")
        print(f"python3 {Path(__file__).resolve()} --confirm --send --confirm-send")
        log_line(
            config,
            f"send-review-required source={source_file} rows={len(records)} sheets={','.join(sheet_names)} to_send={len(to_create)}",
        )
        return 0

    if config.get("require_review", True) and not args.confirm:
        for index in to_create:
            records[index]["_process_status"] = "待建立草稿（需確認）"
        print_summary(source_file, records, to_create, daily_remaining)
        print("\n目前 require_review=true，尚未建立 Gmail 草稿。確認後請改用：")
        print(f"python3 {Path(__file__).resolve()} --confirm")
        log_line(
            config,
            f"review-required source={source_file} rows={len(records)} sheets={','.join(sheet_names)} to_create={len(to_create)}",
        )
        return 0

    foreign_language_indices = [
        index for index in to_create if records[index].get("_template_language") and records[index].get("_template_language") != "zh"
    ]
    if foreign_language_indices and not args.confirm_foreign_languages:
        for index in to_create:
            records[index]["_process_status"] = "待送出（需確認）" if args.send else "待建立草稿（需確認）"
        print_summary(source_file, records, to_create, daily_remaining, "直接寄出" if args.send else "建立草稿")
        print("\n本批包含外語草稿，尚未建立 Gmail 草稿，也沒有送出。請先確認上方語言判斷是否正確。")
        print("確認後請改用：")
        if args.send:
            print(f"python3 {Path(__file__).resolve()} --confirm --send --confirm-send --confirm-foreign-languages")
        else:
            print(f"python3 {Path(__file__).resolve()} --confirm --confirm-foreign-languages")
        log_line(
            config,
            "foreign-language-review-required "
            f"source={source_file} rows={len(records)} sheets={','.join(sheet_names)} "
            f"to_create={len(to_create)} foreign={len(foreign_language_indices)}",
        )
        return 0

    created_history_rows: list[dict[str, str]] = []
    sent_rows: list[dict[str, str]] = []
    gmail_client: GmailDraftClient | None = None
    if to_create:
        try:
            gmail_client = GmailDraftClient.from_env(config)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            log_line(config, f"gmail-not-configured source={source_file} error={exc}")
            return 2

    for index in to_create:
        record = records[index]
        assert gmail_client is not None
        template_subject, template_body, template_language = template_for_record(templates, record)
        subject = render_text(template_subject, record)
        body = render_text(template_body, record)
        email = record.get("_email_normalized", "")
        try:
            draft_id = gmail_client.create_draft(email, subject, body)
            record["_draft_id"] = draft_id
            created_history_rows.append(
                {
                    "created_at": timestamp(),
                    "email": email,
                    "店家名稱": str(record.get("店家名稱", "")),
                    "source_file": str(source_file.resolve()),
                    "source_sheet": str(record.get("_source_sheet", "")),
                    "source_row": str(record.get("_source_row", "")),
                    "draft_id": draft_id,
                    "subject": subject,
                }
            )
            if args.send:
                message = gmail_client.send_draft(draft_id)
                record["_message_id"] = str(message.get("id", ""))
                record["_thread_id"] = str(message.get("threadId", ""))
                record["_process_status"] = "已送出"
                sent_rows.append(
                    {
                        "sent_at": timestamp(),
                        "draft_id": draft_id,
                        "message_id": record["_message_id"],
                        "thread_id": record["_thread_id"],
                        "email": email,
                        "店家名稱": str(record.get("店家名稱", "")),
                        "subject": subject,
                        "status": "已送出",
                        "error": "",
                    }
                )
            else:
                record["_process_status"] = "已建立草稿"
            time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001 - keep row-level failure in the report.
            record["_process_status"] = "送出失敗" if args.send and record.get("_draft_id") else "建立草稿失敗"
            record["_error"] = str(exc)

    if created_history_rows:
        write_history(history_file, created_history_rows)
    if sent_rows:
        write_sent_rows(resolve_path(config, "sent_history_file"), sent_rows)

    processed_folder = resolve_path(config, "processed_folder")
    report_file = write_processed_report(processed_folder, source_file, records)
    if all_terminal(records):
        mark_source_processed(resolve_path(config, "processed_files_file"), source_file, report_file, records)

    print_summary(source_file, records, to_create, daily_remaining, "直接寄出" if args.send else "建立草稿")
    print(f"\n處理報告：{report_file}")
    if all_terminal(records):
        print("來源檔已標記為處理完成。")
    else:
        print("來源檔尚未標記為處理完成；仍有批次上限、每日上限或建立失敗的項目可供下次處理。")

    log_line(
        config,
        "completed "
        f"source={source_file} rows={len(records)} created={len(created_history_rows)} "
        f"report={report_file} terminal={all_terminal(records)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
