"""
air.py - 제주 미세먼지 API (공공데이터 -> 가공 -> 예측 -> Supabase 저장)

  공공데이터 API --1) 받기--> FastAPI --2) 가공 --3) 예측--> 4) Supabase 저장
  Vercel(프론트)은 결과만 요청한다. 키는 전부 서버(Railway Variables) 안에만 있다.

환경변수
  DATA_GO_KR_KEY    공공데이터포털 인증키 (없으면 연습 데이터)
  ADMIN_TOKEN       POST /collect 를 아무나 못 누르게 하는 암호
  SIDO_NAME=제주    COLLECT_MINUTES=60    AUTO_COLLECT=1    DEMO_MODE=0
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query

import air_collect as collect
import air_db as db
import air_predict as predict

log = logging.getLogger("uvicorn.error")

DATA_KEY = os.getenv("DATA_GO_KR_KEY", "").strip()
SIDO = os.getenv("SIDO_NAME", "제주").strip()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
DEMO = os.getenv("DEMO_MODE", "0") == "1" or not DATA_KEY
SOURCE = "demo" if DEMO else "airkorea"
AUTO = os.getenv("AUTO_COLLECT", "1") == "1"
MINUTES = int(os.getenv("COLLECT_MINUTES", "60"))

store = db.get_store()
router = APIRouter()


# ─────────────── 수집 한 번 = 받기 -> 가공 -> 저장 -> 기록 ───────────────
def run_collect(hours: int = 1) -> dict:
    items = collect.fake_raw(hours) if DEMO else collect.fetch_raw(SIDO, DATA_KEY)
    rows, stats = collect.clean(items, SIDO, SOURCE)
    saved = store.save_readings(rows)
    entry = {"ran_at": datetime.now(timezone.utc).isoformat(), "source": SOURCE,
             **stats, "saved": saved}
    store.save_log(entry)
    log.info("collect %s", entry)
    return entry


BACKFILL_MIN = 24  # 측정소별로 이 개수보다 적게 쌓여 있으면 과거 24시간을 채운다


def run_backfill(only_missing: bool = True) -> dict:
    """측정소별 최근 24시간 기록을 받아 DB에 채운다 (이미 있는 시각은 덮어써서 중복 없음)."""
    if DEMO:
        return {"skipped": "연습 모드에서는 필요 없어요"}
    stations = sorted({i["stationName"] for i in collect.fetch_raw(SIDO, DATA_KEY)
                       if i.get("stationName")})
    total = {"fetched": 0, "fixed_values": 0, "dropped": 0, "cleaned": 0}
    saved, done = 0, 0
    for st in stations:
        if only_missing and len(store.history(SOURCE, st)) >= BACKFILL_MIN:
            continue
        try:
            rows, stats = collect.clean(collect.fetch_station_history(st, DATA_KEY), SIDO, SOURCE)
        except Exception:
            log.exception("과거 기록 채우기 실패: %s", st)
            continue
        saved += store.save_readings(rows)
        for k in total:
            total[k] += stats[k]
        done += 1
    entry = {"ran_at": datetime.now(timezone.utc).isoformat(), "source": SOURCE,
             **total, "saved": saved}
    if done:
        store.save_log(entry)
    log.info("backfill stations=%s %s", done, entry)
    return {"stations_filled": done, "stations_total": len(stations), **entry}


def _seconds_until_next_run() -> float:
    """COLLECT_MINUTES=60이면 매 시각 10분(KST)에 맞춰 수집 -> 하루에 24번, 같은 시각 기록이 쌓인다."""
    if MINUTES != 60:
        return MINUTES * 60
    now = datetime.now(collect.KST)
    nxt = now.replace(minute=10, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()


async def auto_loop():
    """서버가 켜져 있는 동안 정해진 시각마다 자동 수집 (브라우저가 꺼져 있어도!)"""
    # 처음 켜질 때: DB가 비어 있으면 예측을 바로 볼 수 있게 과거 기록을 먼저 채운다
    try:
        if DEMO and not store.recent(SOURCE, 1):
            await asyncio.to_thread(run_collect, 72)
        elif not DEMO:
            await asyncio.to_thread(run_backfill)
    except Exception:
        log.exception("초기 데이터 채우기 실패")
    while True:
        try:
            await asyncio.to_thread(run_collect)
        except Exception:
            log.exception("자동 수집 실패")
        await asyncio.sleep(_seconds_until_next_run())


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(auto_loop()) if AUTO else None
    yield
    if task:
        task.cancel()


# ─────────────── API ───────────────
@router.get("/air/health")
def air_health():
    missing = [k for k in ["DATA_GO_KR_KEY", "ADMIN_TOKEN"] if not os.getenv(k, "").strip()]
    return {"ok": True, "mode": SOURCE, "store": store.kind,
            "auto_collect_minutes": MINUTES if AUTO else None, "missing": missing}


@router.post("/collect")
def collect_now(x_admin_token: str = Header(default=""),
                hours: int = Query(1, ge=1, le=168, description="연습 모드에서만: 몇 시간치 만들지")):
    """지금 바로 한 번 수집 (Swagger /docs 에서 Try it out)"""
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Railway Variables에 ADMIN_TOKEN을 먼저 넣으세요")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "x-admin-token이 틀렸습니다")
    try:
        return run_collect(hours if DEMO else 1)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.post("/backfill")
def backfill_now(x_admin_token: str = Header(default=""),
                 force: bool = Query(False, description="true면 이미 충분히 쌓인 측정소도 다시 받음")):
    """측정소별 과거 24시간 기록을 지금 DB에 채운다"""
    if not ADMIN_TOKEN:
        raise HTTPException(503, "Railway Variables에 ADMIN_TOKEN을 먼저 넣으세요")
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "x-admin-token이 틀렸습니다")
    try:
        return run_backfill(only_missing=not force)
    except Exception as e:
        raise HTTPException(502, str(e))


@router.get("/air/history")
def air_history(station: str, hours: int = Query(48, ge=1, le=168)):
    """DB에 쌓인 시간별 기록(시계열)과, 그 기록으로 만든 다음 1시간 예측"""
    hist = store.history(SOURCE, station, limit=hours)
    if not hist:
        raise HTTPException(404, f"'{station}' 측정소 데이터가 없습니다")
    points = [{"time": db.kst(h["data_time"]), "pm10": h["pm10"], "pm25": h["pm25"]} for h in hist]
    return {"station": station, "mode": SOURCE, "count": len(points), "points": points,
            "prediction": predict.predict_next(hist)}


def _latest_rows():
    rows = store.recent(SOURCE)
    seen, latest = set(), []
    for r in rows:  # 시각 내림차순 -> 측정소별 첫 행이 최신
        if r["station"] not in seen:
            seen.add(r["station"])
            latest.append(r)
    return latest


@router.get("/air/latest")
def air_latest():
    rows = _latest_rows()
    rows.sort(key=lambda r: r["pm10"] or 0, reverse=True)
    return [{"station": r["station"], "time": db.kst(r["data_time"]),
             "pm10": r["pm10"], "pm10_grade": r["pm10_grade"],
             "pm25": r["pm25"], "pm25_grade": r["pm25_grade"]} for r in rows]


@router.get("/air/summary")
def air_summary():
    rows = _latest_rows()
    pm10s = [r["pm10"] for r in rows if r["pm10"] is not None]
    pm25s = [r["pm25"] for r in rows if r["pm25"] is not None]
    worst = max(rows, key=lambda r: r["pm10"] or 0) if rows else None
    logs = store.logs(1)
    avg10 = round(sum(pm10s) / len(pm10s)) if pm10s else None
    return {
        "mode": SOURCE,
        "stations": len(rows),
        "avg_pm10": avg10,
        "avg_pm25": round(sum(pm25s) / len(pm25s)) if pm25s else None,
        "avg_pm10_grade": collect.grade_pm10(avg10) if avg10 is not None else None,
        "worst_station": worst and {"station": worst["station"], "pm10": worst["pm10"]},
        "last_collect": logs[0] if logs else None,
    }


@router.get("/air/predict")
def air_predict(station: str):
    hist = store.history(SOURCE, station)
    if not hist:
        raise HTTPException(404, f"'{station}' 측정소 데이터가 없습니다")
    return {"station": station, **predict.predict_next(hist)}


@router.get("/logs")
def logs():
    return store.logs()
