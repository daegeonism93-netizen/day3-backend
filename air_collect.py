"""
air_collect.py - 1) 공공데이터 받기, 2) 데이터 가공(정리)

에어코리아 '시도별 실시간 측정정보' API(공공데이터포털 data.go.kr)를 받아서
지저분한 원본을 DB에 넣기 좋은 깔끔한 행(row)으로 바꿉니다.
"""
import math
import random
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

import httpx
import pandas as pd

KST = timezone(timedelta(hours=9))
API_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getCtprvnRltmMesureDnsty"


# ─────────────────────────────────────────────
# 1) 받기 - 진짜 공공데이터 API
# ─────────────────────────────────────────────
def fetch_raw(sido: str, service_key: str) -> list[dict]:
    """에어코리아에서 '지금 시각' 측정소별 원본 데이터를 받아 온다."""
    params = {
        # 포털의 Encoding 키를 붙여 넣어도 되도록 한 번 풀어 준다 (httpx가 다시 인코딩함)
        "serviceKey": unquote(service_key),
        "returnType": "json",
        "numOfRows": "100",
        "pageNo": "1",
        "sidoName": sido,
        "ver": "1.0",
    }
    r = httpx.get(API_URL, params=params, timeout=20)
    r.raise_for_status()
    text = r.text.strip()
    # 키가 틀리면 JSON을 달라고 해도 XML 오류가 온다
    if not text.startswith("{"):
        raise RuntimeError("공공데이터 API 오류(키 확인): " + text[:200])
    body = r.json()["response"]
    if body["header"].get("resultCode") != "00":
        raise RuntimeError("공공데이터 API 오류: " + str(body["header"]))
    return body["body"].get("items") or []


# ─────────────────────────────────────────────
# 1') 연습용 가짜 원본 - API 키 승인 전에도 실습할 수 있게
#     (진짜 API처럼 일부러 지저분하게 만든다)
# ─────────────────────────────────────────────
DEMO_STATIONS = ["이도동", "연동", "동홍동", "고산리", "성산읍"]


def fake_raw(hours: int = 1) -> list[dict]:
    now = datetime.now(KST).replace(minute=0, second=0, microsecond=0)
    items = []
    for back in range(hours - 1, -1, -1):
        t = now - timedelta(hours=back)
        for i, st in enumerate(DEMO_STATIONS):
            rnd = random.Random(f"{st}-{t:%Y%m%d%H}")  # 같은 시각엔 같은 값
            wave = 18 * (1 + math.sin((t.hour - 9) / 24 * 6.283))
            pm10 = 20 + i * 6 + wave + rnd.gauss(0, 6)
            pm25 = pm10 * 0.45 + rnd.gauss(0, 3)
            item = {
                "stationName": st,
                # 에어코리아는 자정을 '24:00'으로 보낸다 -> 가공 때 고쳐야 함
                "dataTime": (f"{(t - timedelta(days=1)):%Y-%m-%d} 24:00" if t.hour == 0
                             else f"{t:%Y-%m-%d %H}:00"),
                "pm10Value": str(max(0, round(pm10))),  # 숫자가 '문자'로 온다
                "pm25Value": str(max(0, round(pm25))),
                "o3Value": f"{0.02 + rnd.random() * 0.03:.3f}",
                "no2Value": f"{0.003 + rnd.random() * 0.01:.3f}",
                "pm10Flag": None,
                "pm25Flag": None,
            }
            r = rnd.random()
            if r < 0.07:  # 결측: '-'
                item["pm10Value"] = "-"
                item["pm25Value"] = "-"
            elif r < 0.10:  # 장비 점검 표시
                item["pm10Flag"] = "통신장애"
            elif r < 0.12:  # 말이 안 되는 값
                item["pm25Value"] = "-999"
            items.append(item)
    return items


# ─────────────────────────────────────────────
# 2) 가공 - pandas로 정리
# ─────────────────────────────────────────────
def grade_pm10(v):
    if v is None:
        return None
    return "좋음" if v <= 30 else "보통" if v <= 80 else "나쁨" if v <= 150 else "매우나쁨"


def grade_pm25(v):
    if v is None:
        return None
    return "좋음" if v <= 15 else "보통" if v <= 35 else "나쁨" if v <= 75 else "매우나쁨"


def parse_time(s: str) -> str | None:
    """'2026-11-10 24:00' 같은 시각을 '2026-11-11T00:00:00+09:00'으로."""
    try:
        day, hm = str(s).split(" ")
        h = int(hm.split(":")[0])
        t = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=KST) + timedelta(hours=h)
        return t.isoformat()
    except Exception:
        return None


def _num(v):
    return None if pd.isna(v) else float(v)


def clean(items: list[dict], sido: str, source: str):
    """원본 -> (DB에 넣을 행 목록, 가공 통계)"""
    stats = {"fetched": len(items), "fixed_values": 0, "dropped": 0, "cleaned": 0}
    if not items:
        return [], stats
    df = pd.DataFrame(items)
    for col in ["pm10Value", "pm25Value", "o3Value", "no2Value", "pm10Flag", "pm25Flag"]:
        if col not in df:
            df[col] = None
    df = df.rename(columns={"stationName": "station", "dataTime": "data_time",
                            "pm10Value": "pm10", "pm25Value": "pm25",
                            "o3Value": "o3", "no2Value": "no2"})
    # 1) '-'·빈칸 -> 빈값, '23' 같은 글자 -> 숫자 23
    for col in ["pm10", "pm25", "o3", "no2"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # 2) 장비 이상 표시(flag)가 붙은 값은 믿지 않는다
    bad10 = df["pm10Flag"].notna() & df["pm10"].notna()
    bad25 = df["pm25Flag"].notna() & df["pm25"].notna()
    df.loc[bad10, "pm10"] = None
    df.loc[bad25, "pm25"] = None
    # 3) 말이 안 되는 값(음수, 1000 초과) 지우기
    out10 = (df["pm10"] < 0) | (df["pm10"] > 1000)
    out25 = (df["pm25"] < 0) | (df["pm25"] > 1000)
    df.loc[out10, "pm10"] = None
    df.loc[out25, "pm25"] = None
    stats["fixed_values"] = int(bad10.sum() + bad25.sum() + out10.sum() + out25.sum())
    # 4) '24:00' -> 다음 날 00:00, 한국 시간대 붙이기
    df["data_time"] = df["data_time"].map(parse_time)
    # 5) 미세먼지 값이 하나도 없거나 시각이 깨진 행은 버린다
    before = len(df)
    df = df.dropna(subset=["data_time"])
    df = df[df["pm10"].notna() | df["pm25"].notna()]
    stats["dropped"] = before - len(df)
    # 6) 등급 계산 후 DB 행으로
    rows = []
    for r in df.itertuples():
        pm10 = None if pd.isna(r.pm10) else int(round(r.pm10))
        pm25 = None if pd.isna(r.pm25) else int(round(r.pm25))
        rows.append({
            "source": source, "sido": sido, "station": r.station,
            "data_time": r.data_time,
            "pm10": pm10, "pm25": pm25,
            "o3": _num(r.o3), "no2": _num(r.no2),
            "pm10_grade": grade_pm10(pm10), "pm25_grade": grade_pm25(pm25),
        })
    stats["cleaned"] = len(rows)
    return rows, stats
