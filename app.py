from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("TWSE_DB_PATH", ROOT / "data" / "twse.sqlite3"))
RAW_DIR = ROOT / "data" / "raw"
TZ = ZoneInfo("Asia/Taipei")
URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
app = Flask(__name__)
update_lock = threading.Lock()

FIELDS = {
    "code": "證券代號", "name": "證券名稱",
    "foreign_buy": "外陸資買進股數(不含外資自營商)",
    "foreign_sell": "外陸資賣出股數(不含外資自營商)",
    "trust_buy": "投信買進股數", "trust_sell": "投信賣出股數",
    "dealer_buy": "自營商買進股數(自行買賣)", "dealer_sell": "自營商賣出股數(自行買賣)",
    "dealer_hedge_buy": "自營商買進股數(避險)", "dealer_hedge_sell": "自營商賣出股數(避險)",
    "total_net": "三大法人買賣超股數",
}
INVESTORS = {
    "foreign": ("外資", "foreign_buy", "foreign_sell"),
    "trust": ("投信", "trust_buy", "trust_sell"),
    "dealer": ("自營商", "dealer_buy", "dealer_sell"),
    "total": ("三大法人合計", None, None),
}


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
          trade_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
          market TEXT NOT NULL DEFAULT '上市', investor TEXT NOT NULL,
          buy_shares INTEGER NOT NULL, sell_shares INTEGER NOT NULL, net_shares INTEGER NOT NULL,
          source TEXT NOT NULL, captured_at TEXT NOT NULL,
          PRIMARY KEY (trade_date, code, investor, market)
        );
        CREATE INDEX IF NOT EXISTS idx_trades_date_investor ON trades(trade_date, investor);
        CREATE INDEX IF NOT EXISTS idx_trades_code_date ON trades(code, trade_date);
        CREATE TABLE IF NOT EXISTS updates (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          finished_at TEXT, status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
          rows_written INTEGER NOT NULL DEFAULT 0
        );
        """)
        db.execute("UPDATE updates SET status='error',finished_at=?,message='更新程序中斷，請重新手動更新' WHERE status='running'",
                   (datetime.now(TZ).isoformat(timespec="seconds"),))


def number(value):
    result = int(str(value).replace(",", "").strip())
    return result


def parse_payload(payload, expected_date):
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        status = payload.get("stat", "未知回應") if isinstance(payload, dict) else "非 JSON 物件"
        if status in ("查無資料", "很抱歉，沒有符合條件的資料!"):
            return []
        raise ValueError(f"TWSE 回應狀態異常：{status}")
    fields = payload.get("fields")
    rows = payload.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list) or payload.get("date") != expected_date:
        raise ValueError("TWSE 回應缺少欄位、資料列或日期不符，已停止寫入。")
    positions = {name: fields.index(label) for name, label in FIELDS.items() if label in fields}
    missing = [label for label in FIELDS.values() if label not in fields]
    if missing:
        raise ValueError("TWSE 欄位格式有變，缺少：" + "、".join(missing))
    day = datetime.strptime(expected_date, "%Y%m%d").date().isoformat()
    captured = datetime.now(TZ).isoformat(timespec="seconds")
    records = []
    for row in rows:
        if not isinstance(row, list) or len(row) < len(fields):
            raise ValueError("TWSE 資料列長度與欄位不符，已停止寫入。")
        code = str(row[positions["code"]]).strip()
        name = str(row[positions["name"]]).strip()
        vals = {key: number(row[positions[key]]) for key in positions if key not in ("code", "name")}
        foreign = (vals["foreign_buy"], vals["foreign_sell"])
        trust = (vals["trust_buy"], vals["trust_sell"])
        dealer_buy = vals["dealer_buy"] + vals["dealer_hedge_buy"]
        dealer_sell = vals["dealer_sell"] + vals["dealer_hedge_sell"]
        dealer = (dealer_buy, dealer_sell)
        total = vals["total_net"]
        if not code or not name or foreign[0] - foreign[1] + trust[0] - trust[1] + dealer_buy - dealer_sell != total:
            raise ValueError(f"{code or '未知代碼'} 買賣超驗算失敗，已停止寫入。")
        for investor, (buy, sell) in {"foreign": foreign, "trust": trust, "dealer": dealer}.items():
            records.append((day, code, name, "上市", investor, buy, sell, buy - sell, "TWSE T86", captured))
        records.append((day, code, name, "上市", "total", foreign[0] + trust[0] + dealer_buy,
                        foreign[1] + trust[1] + dealer_sell, total, "TWSE T86", captured))
    return records


def fetch_day(day):
    ymd = day.strftime("%Y%m%d")
    response = requests.get(URL, params={"response": "json", "date": ymd, "selectType": "ALLBUT0999"},
                            timeout=25, headers={"User-Agent": "TWSE-Institutional-Flow/1.0"})
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict) and payload.get("stat") == "OK":
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        (RAW_DIR / f"T86_{ymd}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def update_history(days=40):
    if not update_lock.acquire(blocking=False):
        raise RuntimeError("更新作業正在執行，請稍後再試。")
    started = datetime.now(TZ).isoformat(timespec="seconds")
    with connect() as db:
        cur = db.execute("INSERT INTO updates(started_at,status,message) VALUES(?,?,?)", (started, "running", "正在查詢 TWSE T86"))
        update_id = cur.lastrowid
    count = 0
    try:
        today = datetime.now(TZ).date()
        for offset in range(days):
            day = today - timedelta(days=offset)
            try:
                payload = fetch_day(day)
                records = parse_payload(payload, day.strftime("%Y%m%d"))
            except requests.exceptions.JSONDecodeError as exc:
                raise RuntimeError(f"TWSE 回應不是有效 JSON（{day.isoformat()}）：{exc}") from exc
            except requests.RequestException as exc:
                raise RuntimeError(f"連線 TWSE 失敗（{day.isoformat()}）：{exc}") from exc
            except (ValueError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"資料解析失敗（{day.isoformat()}）：{exc}") from exc
            if records:
                with connect() as db:
                    db.executemany("""INSERT INTO trades VALUES(?,?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(trade_date,code,investor,market) DO UPDATE SET
                      name=excluded.name,buy_shares=excluded.buy_shares,sell_shares=excluded.sell_shares,
                      net_shares=excluded.net_shares,source=excluded.source,captured_at=excluded.captured_at""", records)
                count += len(records)
            time.sleep(0.15)
        finished = datetime.now(TZ).isoformat(timespec="seconds")
        with connect() as db:
            db.execute("UPDATE updates SET finished_at=?,status='success',message=?,rows_written=? WHERE id=?",
                       (finished, f"完成；處理 {days} 個日曆日", count, update_id))
        return {"status": "success", "finished_at": finished, "rows_written": count}
    except Exception as exc:
        finished = datetime.now(TZ).isoformat(timespec="seconds")
        with connect() as db:
            db.execute("UPDATE updates SET finished_at=?,status='error',message=?,rows_written=? WHERE id=?",
                       (finished, str(exc), count, update_id))
        raise
    finally:
        update_lock.release()


def current_info(db):
    latest = db.execute("SELECT MAX(trade_date) FROM trades").fetchone()[0]
    last = db.execute("SELECT finished_at,status,message FROM updates ORDER BY id DESC LIMIT 1").fetchone()
    successful = db.execute("SELECT finished_at FROM updates WHERE status='success' ORDER BY id DESC LIMIT 1").fetchone()
    return {"data_date": latest, "updated_at": successful["finished_at"] if successful else None,
            "update_status": last["status"] if last else "never", "update_message": last["message"] if last else "尚未更新",
            "is_stale": bool(latest and latest < datetime.now(TZ).date().isoformat())}


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/status")
def status():
    with connect() as db:
        return jsonify(current_info(db))


@app.get("/api/rankings")
def rankings():
    investor = request.args.get("investor", "foreign")
    direction = request.args.get("direction", "buy")
    search = request.args.get("q", "").strip()
    if investor not in INVESTORS or direction not in ("buy", "sell"):
        return jsonify({"error": "排行條件不正確。"}), 400
    with connect() as db:
        info = current_info(db)
        if not info["data_date"]: return jsonify({**info, "rows": []})
        col = "net_shares"
        order = "DESC" if direction == "buy" else "ASC"
        rows = db.execute(f"SELECT code,name,buy_shares,sell_shares,net_shares,trade_date FROM trades WHERE trade_date=? AND investor=? AND (code LIKE ? OR name LIKE ?) ORDER BY net_shares {order} LIMIT 200",
                          (info["data_date"], investor, f"%{search}%", f"%{search}%")).fetchall()
        return jsonify({**info, "investor_name": INVESTORS[investor][0], "direction": direction,
                        "rows": [dict(r) for r in rows]})


@app.get("/api/stock/<code>")
def stock(code):
    with connect() as db:
        info = current_info(db)
        rows = db.execute("SELECT * FROM trades WHERE code=? ORDER BY trade_date DESC LIMIT 80", (code,)).fetchall()
    if not rows: return jsonify({"error": f"查無 {code} 的法人資料，請確認代碼或先更新資料。"}), 404
    by_date = {}
    for r in rows:
        by_date.setdefault(r["trade_date"], {"date": r["trade_date"], "code": r["code"], "name": r["name"]})[r["investor"]] = r["net_shares"]
    days = sorted(by_date.values(), key=lambda x: x["date"])[-20:]
    for row in days:
        for investor in INVESTORS: row.setdefault(investor, 0)
    sums = {key: sum(row[key] for row in days) for key in INVESTORS}
    return jsonify({**info, "days": days, "cumulative": sums})


@app.post("/api/update")
def update():
    try:
        result = update_history()
        return jsonify(result)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 502


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
else:
    init_db()
