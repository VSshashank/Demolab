"""Route polylines for the Mangalore region, plus the geometry helpers.

Anchor waypoints are hand-authored along the real road corridors and then
densified by linear interpolation to ~50 m spacing. Real surveyed road geometry
is not required for this build -- what matters is that the polyline is smooth,
lands in the right place on the map, and carries a plausible speed limit and
grade per segment so the road-load model has something to chew on.

Nothing here touches the network. If a routing API is ever used to improve the
geometry, the result must be cached to a committed JSON file, because the demo
has to run with the wifi off.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

TARGET_SPACING_M = 50.0
EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial compass bearing from point 1 to point 2, 0-360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def offset_point(lat: float, lon: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Move a coordinate `distance_m` along `bearing`. Used by ROUTE_DEVIATION."""
    br = math.radians(bearing)
    d = distance_m / EARTH_RADIUS_M
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(d) * math.cos(p1), math.cos(d) - math.sin(p1) * math.sin(p2)
    )
    return math.degrees(p2), math.degrees(l2)


@dataclass
class RoutePoint:
    lat: float
    lon: float
    speed_limit_kmh: float
    cum_m: float  # distance from route start
    grade_rad: float
    alt_m: float


@dataclass
class Route:
    key: str
    name: str
    description: str
    default_limit_kmh: float
    points: list[RoutePoint] = field(default_factory=list)

    @property
    def length_m(self) -> float:
        return self.points[-1].cum_m if self.points else 0.0

    @property
    def length_km(self) -> float:
        return self.length_m / 1000.0

    def polyline(self) -> list[list[float]]:
        """[[lat, lon], ...] for Leaflet. Thinned -- the browser does not need 50 m."""
        step = max(1, len(self.points) // 400)
        pts = [[p.lat, p.lon] for p in self.points[::step]]
        last = [self.points[-1].lat, self.points[-1].lon]
        if pts[-1] != last:
            pts.append(last)
        return pts

    def point_at(self, cum_m: float) -> RoutePoint:
        """Nearest densified sample at or before `cum_m`, clamped to the route."""
        if cum_m <= 0:
            return self.points[0]
        idx = int(cum_m / TARGET_SPACING_M)
        return self.points[min(idx, len(self.points) - 1)]

    def distance_to_m(self, lat: float, lon: float) -> float:
        """Shortest distance from a fix to the polyline. Feeds ROUTE_DEVIATION.

        Samples every 10th densified point (~500 m). At a 500 m alert threshold
        that coarseness is immaterial and it keeps the check cheap enough to run
        on every packet.
        """
        return min(haversine_m(lat, lon, p.lat, p.lon) for p in self.points[::10])


# ---------------------------------------------------------------------------
# Anchor waypoints. (lat, lon, speed_limit_kmh) along the real corridors.
# `relief_m` is the peak-to-peak terrain range the route crosses; the grade
# profile is derived from it rather than the other way round, so the altitude
# a route reports can never drift away from the grade the physics is fed.
# ---------------------------------------------------------------------------
_ANCHORS: dict[str, dict] = {
    "NH66_MNG_UDUPI": {
        "name": "NH-66 Mangalore - Surathkal - Udupi",
        "description": "Coastal national highway run, long high-speed cruise legs",
        "limit": 80.0,
        "base_alt_m": 8.0,
        "relief_m": 62.0,
        "pts": [
            (12.8698, 74.8431, 40),   # Hampankatta, Mangalore city centre
            (12.8823, 74.8385, 50),   # Kottara Chowki
            (12.9081, 74.8291, 60),   # Kulur bridge
            (12.9350, 74.8140, 70),   # Baikampady
            (12.9700, 74.8040, 80),   # Panambur
            (13.0068, 74.7940, 80),   # Surathkal
            (13.0450, 74.7910, 80),   # Mukka
            (13.0680, 74.7960, 80),   # Hosabettu
            (13.0906, 74.7889, 80),   # Mulki
            (13.1050, 74.7920, 80),   # Bappanadu
            (13.1240, 74.7800, 80),   # Hejamadi
            (13.1447, 74.7688, 80),   # Padubidri
            (13.1700, 74.7620, 80),   # Uchila
            (13.1950, 74.7520, 80),   # Nandikoor
            (13.2264, 74.7458, 80),   # Kaup
            (13.2560, 74.7500, 80),   # Padu
            (13.2850, 74.7430, 70),   # Katapadi
            (13.3150, 74.7520, 60),   # Santhekatte
            (13.3409, 74.7421, 40),   # Udupi
        ],
    },
    "PORT_BANTWAL": {
        "name": "New Mangalore Port - B.C. Road - Bantwal",
        "description": "Container haul inland, mixed urban and state highway",
        "limit": 60.0,
        "base_alt_m": 10.0,
        "relief_m": 84.0,
        "pts": [
            (12.9256, 74.8021, 30),   # New Mangalore Port gate
            (12.9180, 74.8180, 40),   # Baikampady industrial area
            (12.9081, 74.8291, 50),   # Kulur
            (12.8896, 74.8467, 40),   # Kottara junction
            (12.8747, 74.8730, 40),   # Nanthoor
            (12.8842, 74.9054, 60),   # Vamanjoor
            (12.9169, 74.9186, 60),   # Gurupura
            (12.9404, 74.9311, 60),   # Kaikamba
            (12.9200, 74.9700, 60),   # Addoor
            (12.8820, 74.9700, 60),   # Farangipete
            (12.8855, 75.0166, 50),   # B.C. Road
            (12.8900, 75.0350, 40),   # Bantwal
        ],
    },
    "CITY_LOOP": {
        "name": "Mangalore city delivery loop",
        "description": "Dense stop-go distribution round, heavy idling",
        "limit": 40.0,
        "base_alt_m": 12.0,
        "relief_m": 62.0,
        "pts": [
            (12.8698, 74.8431, 40),   # Hampankatta
            (12.8760, 74.8380, 40),   # Lalbagh
            (12.8830, 74.8420, 40),   # Urwa
            (12.9010, 74.8300, 40),   # Kavoor
            (12.8896, 74.8467, 40),   # Kottara
            (12.8790, 74.8690, 40),   # Bejai
            (12.8843, 74.8570, 40),   # Kadri Park
            (12.8747, 74.8730, 40),   # Nanthoor
            (12.8666, 74.8620, 40),   # Kankanady
            (12.8598, 74.8541, 40),   # Pumpwell
            (12.8536, 74.8357, 30),   # Jeppu
            (12.8480, 74.8410, 30),   # Mangaladevi
            (12.8620, 74.8300, 30),   # Bunder
            (12.8698, 74.8431, 40),   # back to Hampankatta
        ],
    },
    "MITE_MOODBIDRI": {
        "name": "Mangalore - Gurupura - MITE Moodbidri",
        "description": "Inland campus run over rolling terrain, sustained grades",
        "limit": 60.0,
        "base_alt_m": 14.0,
        "relief_m": 126.0,
        "pts": [
            (12.8698, 74.8431, 40),   # Hampankatta
            (12.8747, 74.8730, 40),   # Nanthoor
            (12.8842, 74.9054, 50),   # Vamanjoor
            (12.9169, 74.9186, 60),   # Gurupura
            (12.9404, 74.9311, 60),   # Kaikamba
            (12.9620, 74.9420, 60),   # Kinnikambla
            (12.9700, 74.9680, 60),   # Kavoor cross
            (12.9880, 74.9560, 60),   # Punjalakatte link
            (13.0050, 74.9800, 60),   # Palimar
            (13.0180, 74.9600, 60),   # Nellikar
            (13.0261, 74.9740, 60),   # Mijar
            (13.0520, 74.9880, 50),   # Moodbidri outskirts
            (13.0700, 74.9950, 40),   # MITE Moodbidri
        ],
    },
}


# Terrain harmonics. Integer cycle counts, so the profile closes on itself over
# the route and a loop does not end 200 m below where it started.
_TERRAIN_CYCLES = (1, 3, 7)
_TERRAIN_AMPS = (0.60, 0.28, 0.12)


def _terrain(x: float, phases: tuple[float, ...]) -> float:
    """Normalised altitude shape in [-1, 1]. x is fractional distance along the route."""
    return sum(
        a * math.sin(2 * math.pi * c * x + p)
        for a, c, p in zip(_TERRAIN_AMPS, _TERRAIN_CYCLES, phases)
    ) / sum(_TERRAIN_AMPS)


def _terrain_slope(x: float, phases: tuple[float, ...]) -> float:
    """d(normalised altitude)/dx -- the analytic derivative of _terrain."""
    return sum(
        a * 2 * math.pi * c * math.cos(2 * math.pi * c * x + p)
        for a, c, p in zip(_TERRAIN_AMPS, _TERRAIN_CYCLES, phases)
    ) / sum(_TERRAIN_AMPS)


def _densify(anchors: list[tuple[float, float, float]]) -> list[tuple[float, float, float, float]]:
    """Anchors -> ~50 m samples, carrying the segment speed limit through.

    Interpolating in lat/lon degrees is fine over a 60 km span at 13 deg N: the
    error against a proper geodesic is centimetres, and the polyline is drawn on
    a web-Mercator map anyway.
    """
    out: list[tuple[float, float, float, float]] = []
    cum = 0.0
    for i in range(len(anchors) - 1):
        lat1, lon1, lim1 = anchors[i]
        lat2, lon2, _ = anchors[i + 1]
        seg_m = haversine_m(lat1, lon1, lat2, lon2)
        n = max(1, int(round(seg_m / TARGET_SPACING_M)))
        for k in range(n):
            f = k / n
            out.append((lat1 + (lat2 - lat1) * f, lon1 + (lon2 - lon1) * f, lim1, cum + seg_m * f))
        cum += seg_m
    lat, lon, lim = anchors[-1]
    out.append((lat, lon, lim, cum))
    return out


def _build(key: str) -> Route:
    meta = _ANCHORS[key]
    samples = _densify(meta["pts"])
    length_m = samples[-1][3]
    rng = random.Random(abs(hash(key)) % 100_000)
    phases = tuple(rng.uniform(0, 2 * math.pi) for _ in _TERRAIN_CYCLES)

    half_relief = meta["relief_m"] / 2.0
    base = meta["base_alt_m"]

    route = Route(key=key, name=meta["name"], description=meta["description"],
                  default_limit_kmh=meta["limit"])
    for lat, lon, lim, cum in samples:
        x = cum / max(length_m, 1.0)
        alt = base + half_relief * (_terrain(x, phases) + 1.0)
        # Grade is the analytic slope of that same altitude curve, so the alt_m
        # in the GPS block and the theta the road-load model uses are one
        # quantity expressed two ways. Amplitudes are chosen so this stays
        # inside +/-4 % on every route; the clamp is a guard, not a shaper.
        slope = half_relief * _terrain_slope(x, phases) / max(length_m, 1.0)
        slope = max(-0.04, min(0.04, slope))
        route.points.append(RoutePoint(lat, lon, lim, cum, math.atan(slope), alt))
    return route


ROUTES: dict[str, Route] = {k: _build(k) for k in _ANCHORS}
ROUTE_KEYS = list(ROUTES.keys())


def get_route(key: str) -> Route:
    return ROUTES[key]


if __name__ == "__main__":
    print(f"{'route':<18}{'points':>8}{'km':>9}{'limit':>8}   name")
    for k, r in ROUTES.items():
        print(f"{k:<18}{len(r.points):>8}{r.length_km:>9.2f}{r.default_limit_kmh:>8.0f}   {r.name}")
        grades = [p.grade_rad for p in r.points]
        alts = [p.alt_m for p in r.points]
        print(f"{'':<18}grade {math.degrees(min(grades)):+.2f} deg to {math.degrees(max(grades)):+.2f} deg"
              f" | alt {min(alts):.0f}-{max(alts):.0f} m")
