"""
air_db.py - 4) 저장: Supabase(진짜 DB)

SUPABASE_URL / SUPABASE_KEY가 있고 air_readings 테이블이 만들어져 있으면 Supabase에 저장하고,
아니면 서버 메모리에 임시 저장한다(재시작하면 사라짐).
그래서 테이블을 만들기 전에도 서버가 죽지 않고 /air/health 에서 상태를 알려 준다.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
log = logging.getLogger("uvicorn.error")


def kst(ts: str) -> str:
    """DB 시각(UTC 등) -> '2026-11-10 14:00' (한국 시간)"""
    return datetime.fromisoformat(ts).astimezone(KST).strftime("%Y-%m-%d %H:%M")


class SupabaseStore:
    kind = "supabase"

    def __init__(self, url, key):
        from supabase import create_client
        self.sb = create_client(url, key)
        # 테이블이 있는지 확인 (없으면 예외 -> 메모리 저장으로 대체)
        self.sb.table("air_readings").select("id").limit(1).execute()
        self.sb.table("collect_logs").select("id").limit(1).execute()

    def save_readings(self, rows):
        if not rows:
            return 0
        # 같은 측정소·같은 시각이면 새로 넣지 않고 덮어쓴다(중복 방지)
        res = (self.sb.table("air_readings")
               .upsert(rows, on_conflict="source,station,data_time").execute())
        return len(res.data or [])

    def save_log(self, entry):
        self.sb.table("collect_logs").insert(entry).execute()

    def recent(self, source, limit=300):
        res = (self.sb.table("air_readings").select("*").eq("source", source)
               .order("data_time", desc=True).limit(limit).execute())
        return res.data

    def history(self, source, station, limit=72):
        res = (self.sb.table("air_readings").select("data_time,pm10,pm25")
               .eq("source", source).eq("station", station)
               .order("data_time", desc=True).limit(limit).execute())
        return list(reversed(res.data))

    def logs(self, limit=20):
        res = (self.sb.table("collect_logs").select("*")
               .order("ran_at", desc=True).limit(limit).execute())
        return res.data


class MemoryStore:
    kind = "memory(임시)"

    def __init__(self):
        self.rows = {}
        self._logs = []

    def save_readings(self, rows):
        for r in rows:
            self.rows[(r["source"], r["station"], r["data_time"])] = r
        return len(rows)

    def save_log(self, entry):
        self._logs.append(entry)

    def recent(self, source, limit=300):
        rs = [r for r in self.rows.values() if r["source"] == source]
        return sorted(rs, key=lambda r: r["data_time"], reverse=True)[:limit]

    def history(self, source, station, limit=72):
        rs = [r for r in self.rows.values() if r["source"] == source and r["station"] == station]
        return sorted(rs, key=lambda r: r["data_time"])[-limit:]

    def logs(self, limit=20):
        return list(reversed(self._logs))[:limit]


def get_store():
    url = os.getenv("SUPABASE_URL", "").strip()
    key = (os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SECRET_KEY") or "").strip()
    if url and key:
        try:
            return SupabaseStore(url, key)
        except Exception as e:  # 테이블이 아직 없거나 연결 오류
            log.warning("Supabase 저장 사용 불가, 메모리로 대체: %s", str(e)[:200])
    return MemoryStore()
