"""
stations.py - 측정소 위치(위도·경도)와 '가장 가까운 측정소' 찾기

에어코리아 '시도별 실시간 측정정보' 응답에는 좌표가 없다.
그래서 측정소 이름 -> 좌표 표를 따로 둔다. (읍·면·동 중심 근사값)
정확한 좌표가 필요하면 공공데이터포털 '에어코리아 측정소정보'
(MsrstnInfoInqireSvc/getMsrstnList)의 dmX(위도)·dmY(경도)로 바꾸면 된다.
"""
import math

# 이름: (위도, 경도)
COORDS = {
    "이도동": (33.5040, 126.5350),
    "일도동": (33.5130, 126.5320),
    "연동":   (33.4890, 126.4930),
    "노형로": (33.4840, 126.4780),
    "화북동": (33.5180, 126.5680),
    "조천읍": (33.5380, 126.6350),
    "구좌읍": (33.5560, 126.8400),
    "애월읍": (33.4630, 126.3290),
    "한림읍": (33.4110, 126.2690),
    "한경면": (33.3380, 126.1760),
    "고산리": (33.2960, 126.1630),
    "대정읍": (33.2260, 126.2520),
    "안덕면": (33.2520, 126.3270),
    "대륜동": (33.2440, 126.5100),
    "동홍동": (33.2600, 126.5700),
    "남원읍": (33.2790, 126.7190),
    "표선면": (33.3260, 126.8300),
    "성산읍": (33.4370, 126.9000),
    "제주항": (33.5180, 126.5430),  # 화면에 실제로 나온 측정소라 추가
    "강정동": (33.2380, 126.4800),  # 화면에 실제로 나온 측정소라 추가
}


def coord(station: str):
    """측정소 좌표. 표에 없으면 None (지도에 안 찍힘)"""
    return COORDS.get(station)


def distance_km(lat1, lng1, lat2, lng2) -> float:
    """두 지점 사이 거리(km) - 하버사인 공식"""
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest(lat: float, lng: float, rows: list[dict]):
    """클릭한 지점에서 가장 가까운 측정소 행과 거리(km). 좌표 있는 행만 본다."""
    best, best_d = None, None
    for r in rows:
        c = coord(r["station"])
        if not c:
            continue
        d = distance_km(lat, lng, *c)
        if best_d is None or d < best_d:
            best, best_d = r, d
    return best, best_d
