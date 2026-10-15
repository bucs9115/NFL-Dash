#!/usr/bin/env python3
"""
fetch_stats.py — NFL Statistics Dashboard data pipeline
=======================================================

Pulls public nflverse data through `nflreadpy`, aggregates it into
league-wide leaderboards (season-to-date + every individual week) and
per-game matchup breakdowns, then writes a single static JSON file
(`data/stats.json`) consumed by the front-end.

Sources (all nflverse, free & public):
  * load_player_stats(summary_level="week")  - box score + EPA per player/week
  * load_pfr_advstats(stat_type=...)         - PFR advanced: broken tackles, drops,
                                               pressures, coverage allowed (2018+)
  * load_pbp()                               - play-by-play: team totals, red zone,
                                               time of possession, punting, OL proxies,
                                               quarter-by-quarter scoring
  * load_schedules()                         - finals, records, game metadata
  * load_rosters_weekly() / load_players()   - status, rookies, ESPN ids/headshots

NOTE ON WIN RATES: ESPN's Pass/Run Block Win Rate is proprietary tracking data
that nflverse does not publish. This script computes transparent play-by-play
proxies (labelled "PBWR*" / "RBWR*" in the UI):
  * PBWR* = % of dropbacks with no sack and no QB hit
  * RBWR* = % of designed runs gaining 4+ yards (or a first down/TD)

Usage:
    python fetch_stats.py                     # current season + 2 prior
    SEASONS=2023,2024,2025 python fetch_stats.py
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import pandas as pd
import nflreadpy as nfl

OUT_PATH = Path(__file__).resolve().parent / "data" / "stats.json"
TOP_N = 5

# ---------------------------------------------------------------------------
# Franchise metadata: name, primary hex, secondary hex
# ---------------------------------------------------------------------------
TEAMS = {
    "ARI": ("Arizona Cardinals", "#97233F", "#FFB612"),
    "ATL": ("Atlanta Falcons", "#A71930", "#A5ACAF"),
    "BAL": ("Baltimore Ravens", "#241773", "#9E7C0C"),
    "BUF": ("Buffalo Bills", "#00338D", "#C60C30"),
    "CAR": ("Carolina Panthers", "#0085CA", "#101820"),
    "CHI": ("Chicago Bears", "#C83803", "#0B162A"),
    "CIN": ("Cincinnati Bengals", "#FB4F14", "#000000"),
    "CLE": ("Cleveland Browns", "#FF3C00", "#311D00"),
    "DAL": ("Dallas Cowboys", "#003594", "#869397"),
    "DEN": ("Denver Broncos", "#FB4F14", "#002244"),
    "DET": ("Detroit Lions", "#0076B6", "#B0B7BC"),
    "GB":  ("Green Bay Packers", "#203731", "#FFB612"),
    "HOU": ("Houston Texans", "#03202F", "#A71930"),
    "IND": ("Indianapolis Colts", "#002C5F", "#A2AAAD"),
    "JAX": ("Jacksonville Jaguars", "#006778", "#D7A22A"),
    "KC":  ("Kansas City Chiefs", "#E31837", "#FFB81C"),
    "LV":  ("Las Vegas Raiders", "#A5ACAF", "#000000"),
    "LAC": ("Los Angeles Chargers", "#0080C6", "#FFC20E"),
    "LA":  ("Los Angeles Rams", "#003594", "#FFA300"),
    "MIA": ("Miami Dolphins", "#008E97", "#FC4C02"),
    "MIN": ("Minnesota Vikings", "#4F2683", "#FFC62F"),
    "NE":  ("New England Patriots", "#002244", "#C60C30"),
    "NO":  ("New Orleans Saints", "#D3BC8D", "#101820"),
    "NYG": ("New York Giants", "#0B2265", "#A71930"),
    "NYJ": ("New York Jets", "#125740", "#FFFFFF"),
    "PHI": ("Philadelphia Eagles", "#004C54", "#A5ACAF"),
    "PIT": ("Pittsburgh Steelers", "#FFB612", "#101820"),
    "SF":  ("San Francisco 49ers", "#AA0000", "#B3995D"),
    "SEA": ("Seattle Seahawks", "#002244", "#69BE28"),
    "TB":  ("Tampa Bay Buccaneers", "#D50A0A", "#FF7900"),
    "TEN": ("Tennessee Titans", "#0C2340", "#4B92DB"),
    "WAS": ("Washington Commanders", "#5A1414", "#FFB612"),
}
# Historical / alternate abbreviations -> current franchise code
TEAM_ALIAS = {"LAR": "LA", "OAK": "LV", "SD": "LAC", "STL": "LA", "WSH": "WAS"}
# ESPN CDN uses a few different slugs
ESPN_SLUG = {"LA": "lar", "WAS": "wsh"}


def norm_team(t):
    if t is None or (isinstance(t, float) and math.isnan(t)):
        return None
    t = str(t).upper()
    return TEAM_ALIAS.get(t, t)


def logo_url(team):
    return f"https://a.espncdn.com/i/teamlogos/nfl/500/{ESPN_SLUG.get(team, team.lower())}.png"


# ---------------------------------------------------------------------------
# Metric catalogue — single source of truth shared with the front-end.
#   key, label, category, fmt, sort, negative, advanced, entity, filters
#   filters: pos -> allowed position_group list; min -> (column, per-week threshold)
# ---------------------------------------------------------------------------
def M(cat, stat, label, fmt="int", sort="desc", neg=False, adv=False,
      entity="player", pos=None, min_=None, col=None):
    return dict(key=f"{cat}.{stat}", label=label, cat=cat, fmt=fmt, sort=sort,
                neg=neg, adv=adv, entity=entity, pos=pos, min=min_, col=col or stat)


METRICS = [
    # Passing
    M("passing", "pass_yds", "Pass Yards"),
    M("passing", "pass_td", "Passing TDs"),
    M("passing", "cmp_pct", "Completion %", "pct", min_=("attempts", 15)),
    M("passing", "rating", "Passer Rating", "dec1", min_=("attempts", 15)),
    M("passing", "ints", "Interceptions Thrown", neg=True),
    M("passing", "sacked", "Sacks Taken", neg=True),
    M("passing", "air_yds", "Air Yards", adv=True),
    M("passing", "pass_epa_play", "EPA / Dropback", "dec2", adv=True, min_=("attempts", 15)),
    # Rushing
    M("rushing", "rush_yds", "Rush Yards"),
    M("rushing", "rush_td", "Rushing TDs"),
    M("rushing", "ypc", "Yards Per Carry", "dec1", min_=("carries", 8)),
    M("rushing", "broken_tkl", "Broken Tackles", adv=True),
    M("rushing", "fum_lost", "Fumbles Lost", neg=True),
    M("rushing", "rush_epa_play", "EPA / Rush", "dec2", adv=True, min_=("carries", 8)),
    # Receiving
    M("receiving", "rec_yds", "Receiving Yards"),
    M("receiving", "rec", "Receptions"),
    M("receiving", "rec_td", "Receiving TDs"),
    M("receiving", "yac", "Yards After Catch"),
    M("receiving", "targets", "Targets"),
    M("receiving", "tgt_share", "Target Share %", "pct", adv=True, min_=("targets", 3)),
    M("receiving", "drops", "Drops", neg=True, adv=True),
    # Offensive line (team units)
    M("oline", "pbwr", "Pass Block Win Rate*", "pct", adv=True, entity="team"),
    M("oline", "rbwr", "Run Block Win Rate*", "pct", adv=True, entity="team"),
    M("oline", "sacks_allowed", "Sacks Allowed", neg=True, entity="team"),
    M("oline", "ol_penalties", "OL Penalties Committed", neg=True, entity="team"),
    # Defensive line & edge
    M("dline", "sacks", "Sacks", "dec1", pos=["DL"]),
    M("dline", "qb_hits", "QB Hits", pos=["DL"]),
    M("dline", "pressures", "QB Pressures", adv=True, pos=["DL"]),
    M("dline", "tfl", "Tackles for Loss", pos=["DL"]),
    M("dline", "ff", "Forced Fumbles", pos=["DL"]),
    # Linebackers
    M("lb", "tackles", "Total Tackles", pos=["LB"]),
    M("lb", "solo", "Solo Tackles", pos=["LB"]),
    M("lb", "tfl", "Tackles for Loss", pos=["LB"]),
    M("lb", "pbu", "Pass Breakups", pos=["LB"]),
    M("lb", "sacks", "Sacks", "dec1", pos=["LB"]),
    M("lb", "pressures", "QB Pressures", adv=True, pos=["LB"]),
    # Secondary
    M("secondary", "def_int", "Interceptions", pos=["DB"]),
    M("secondary", "pbu", "Pass Deflections", pos=["DB"]),
    M("secondary", "cmp_allowed", "Completion % Allowed", "pct", neg=True,
      adv=True, pos=["DB"], min_=("def_targets", 4)),
    M("secondary", "rating_allowed", "Passer Rating Allowed", "dec1", neg=True,
      adv=True, pos=["DB"], min_=("def_targets", 4)),
    # Special teams
    M("special", "fg_pct", "Field Goal %", "pct", min_=("fg_att", 1)),
    M("special", "fg_pct_u50", "FG % (Under 50)", "pct", min_=("fg_att_u50", 1)),
    M("special", "fg_pct_50", "FG % (50+ Yards)", "pct", min_=("fg_att_50", 0.25)),
    M("special", "pr_avg", "Punt Return Avg", "dec1", min_=("punt_returns", 1)),
    M("special", "kr_avg", "Kick Return Avg", "dec1", min_=("kickoff_returns", 1)),
    M("special", "net_punt", "Net Punting Avg", "dec1", min_=("punts", 2)),
    # Team totals & efficiency
    M("team", "points_for", "Points Scored", entity="team"),
    M("team", "points_against", "Points Allowed", sort="asc", entity="team"),
    M("team", "off_ypg", "Total Offense (YPG)", "dec1", entity="team"),
    M("team", "def_ypg", "Total Defense (YPG Allowed)", "dec1", sort="asc", entity="team"),
    M("team", "takeaways", "Takeaways", entity="team"),
    M("team", "giveaways", "Giveaways", neg=True, entity="team"),
    M("team", "to_diff", "Turnover Differential", "signed", entity="team"),
    M("team", "rz_td_pct", "Red Zone TD %", "pct", entity="team"),
    M("team", "top_avg", "Time of Possession Avg", "time", entity="team"),
    M("team", "off_epa", "Offensive EPA / Play", "dec2", adv=True, entity="team"),
    M("team", "def_epa", "Defensive EPA / Play", "dec2", sort="asc", adv=True, entity="team"),
]

CATEGORIES = [
    {"id": "passing", "label": "Passing", "group": "offense"},
    {"id": "rushing", "label": "Rushing", "group": "offense"},
    {"id": "receiving", "label": "Receiving", "group": "offense"},
    {"id": "oline", "label": "Offensive Line", "group": "offense"},
    {"id": "dline", "label": "Defensive Line & Edge", "group": "defense"},
    {"id": "lb", "label": "Linebackers", "group": "defense"},
    {"id": "secondary", "label": "Secondary", "group": "defense"},
    {"id": "special", "label": "Special Teams", "group": "special"},
    {"id": "team", "label": "Team Totals & Efficiency", "group": "team"},
]

# Raw weekly columns summed per player (missing ones are created as 0)
SUM_COLS = [
    "completions", "attempts", "passing_yards", "passing_tds", "passing_interceptions",
    "sacks_suffered", "passing_air_yards", "passing_epa",
    "carries", "rushing_yards", "rushing_tds", "rushing_fumbles_lost", "rushing_epa",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_yards_after_catch", "receiving_fumbles_lost", "sack_fumbles_lost",
    "def_tackles_solo", "def_tackle_assists", "def_tackles_with_assist", "def_tackles_for_loss",
    "def_sacks", "def_qb_hits", "def_interceptions", "def_pass_defended", "def_fumbles_forced",
    "fumble_recovery_opp", "fumble_recovery_tds",
    "fg_made", "fg_att", "fg_made_50_59", "fg_made_60_", "fg_missed_50_59", "fg_missed_60_",
    "punt_returns", "punt_return_yards", "kickoff_returns", "kickoff_return_yards",
    # merged from PFR advanced / pbp
    "broken_tkl", "drops", "pressures", "def_targets", "def_cmp_allowed",
    "def_yds_allowed", "def_td_allowed", "team_targets", "punts", "net_punt_yds",
]
# Older nflverse column names -> current names
ALIASES = {"interceptions": "passing_interceptions", "sacks": "sacks_suffered"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def log(msg):
    print(f"[fetch_stats] {msg}", flush=True)


def to_pd(df) -> pd.DataFrame:
    """nflreadpy returns polars frames; convert to pandas for convenience."""
    return df.to_pandas() if hasattr(df, "to_pandas") else pd.DataFrame(df)


def safe_load(fn, *args, **kwargs) -> pd.DataFrame:
    try:
        return to_pd(fn(*args, **kwargs))
    except Exception as exc:  # network hiccup, season not yet published, etc.
        log(f"WARN {getattr(fn, '__name__', fn)}{args} failed: {exc}")
        return pd.DataFrame()


def c(df, name, default=0.0):
    return df[name].fillna(default) if name in df.columns else pd.Series(default, index=df.index)


def div(a, b):
    return (a / b.replace(0, float("nan"))).fillna(0)


def clean(v, fmt):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if fmt in ("int", "signed"):
        return int(round(v))
    if fmt == "dec2":
        return round(float(v), 3)
    if fmt == "time":
        return int(round(v))
    return round(float(v), 1)


def passer_rating(cmp_, att, yds, td, ints):
    """Official NFL passer rating (vectorised)."""
    att = att.replace(0, float("nan"))
    clip = lambda s: s.clip(0, 2.375)
    a = clip((cmp_ / att - 0.3) * 5)
    b = clip((yds / att - 3) * 0.25)
    cc = clip(td / att * 20)
    d = clip(2.375 - ints / att * 25)
    return ((a + b + cc + d) / 6 * 100).fillna(0)


def parse_clock(s):
    try:
        m, sec = str(s).split(":")
        return int(m) * 60 + int(sec)
    except Exception:
        return 0


def current_season_guess():
    try:
        return int(nfl.get_current_season())
    except Exception:
        today = dt.date.today()
        return today.year if today.month >= 9 else today.year - 1


# ---------------------------------------------------------------------------
# Player registry (names, positions, headshots, status)
# ---------------------------------------------------------------------------
class Registry:
    def __init__(self):
        self.players: dict[str, dict] = {}
        self.espn: dict[str, str] = {}
        self.pfr_to_gsis: dict[str, str] = {}
        p = safe_load(nfl.load_players)
        if not p.empty and "gsis_id" in p:
            for r in p.itertuples(index=False):
                gid = getattr(r, "gsis_id", None)
                if not gid or not isinstance(gid, str):
                    continue
                eid = getattr(r, "espn_id", None)
                if eid is not None and str(eid) not in ("", "nan", "None"):
                    self.espn[gid] = str(int(float(eid))) if str(eid).replace(".", "").isdigit() else str(eid)
                pfr = getattr(r, "pfr_id", None)
                if isinstance(pfr, str) and pfr:
                    self.pfr_to_gsis[pfr] = gid
        log(f"players index: {len(self.espn)} ESPN ids, {len(self.pfr_to_gsis)} PFR ids")

    def headshot(self, gid, fallback=None):
        if gid in self.espn:  # ESPN "full" headshots are transparent cut-outs
            return f"https://a.espncdn.com/i/headshots/nfl/players/full/{self.espn[gid]}.png"
        return fallback if isinstance(fallback, str) and fallback else None

    def add(self, gid, name, pos, team, headshot=None):
        if not isinstance(gid, str) or not gid:
            return
        cur = self.players.get(gid)
        if cur is None:
            self.players[gid] = {"n": name, "pos": pos, "t": team,
                                 "h": self.headshot(gid, headshot), "s": ["Active"]}
        else:
            cur["t"] = team or cur["t"]
            cur["pos"] = pos or cur["pos"]

    def apply_status(self, rosters: pd.DataFrame, season: int):
        """Use latest weekly roster row of the newest season for status/rookie flags."""
        if rosters.empty or "gsis_id" not in rosters:
            return
        r = rosters.sort_values("week").groupby("gsis_id").tail(1)
        for row in r.itertuples(index=False):
            gid = row.gsis_id
            if gid not in self.players:
                continue
            tags = []
            yexp = getattr(row, "years_exp", None)
            ry = getattr(row, "rookie_year", None)
            if (yexp is not None and yexp == 0) or (ry is not None and ry == season):
                tags.append("Rookie")
            st = str(getattr(row, "status", "") or "").upper()
            desc = str(getattr(row, "status_description_abbr", "") or "").upper()
            if st == "SUS" or desc.startswith("R27") or "SUSP" in desc:
                tags.append("Suspended")
            elif st in ("RES", "PUP", "NON") or desc.startswith(("R01", "R48", "P01")):
                tags.append("IR / Injured")
            else:
                tags.append("Active")
            self.players[gid]["s"] = tags
            if not self.players[gid]["h"]:
                self.players[gid]["h"] = getattr(row, "headshot_url", None)


# ---------------------------------------------------------------------------
# Season loader
# ---------------------------------------------------------------------------
def load_season(season: int, reg: Registry):
    log(f"=== season {season} ===")
    ps = safe_load(nfl.load_player_stats, [season], summary_level="week")
    pbp = safe_load(nfl.load_pbp, [season])
    sched = safe_load(nfl.load_schedules, [season])
    if ps.empty or sched.empty:
        log(f"no data for {season}, skipping")
        return None

    # ---- normalise player weekly frame
    for old, new in ALIASES.items():
        if old in ps and new not in ps:
            ps = ps.rename(columns={old: new})
    if "season_type" in ps:
        ps = ps[ps["season_type"].isin(["REG", "POST"])]
    ps["team"] = (ps["team"] if "team" in ps else ps["recent_team"]).map(norm_team)
    ps["opp"] = c(ps, "opponent_team", None).map(norm_team)
    for col in SUM_COLS:
        if col not in ps:
            ps[col] = 0.0
        ps[col] = pd.to_numeric(ps[col], errors="coerce").fillna(0.0)
    ps["position_group"] = c(ps, "position_group", "")
    ps["position"] = c(ps, "position", "")
    name_col = "player_display_name" if "player_display_name" in ps else "player_name"

    # ---- merge PFR advanced weekly stats (broken tackles, drops, pressures, coverage)
    def pfr(stat_type, mapping):
        df = safe_load(nfl.load_pfr_advstats, [season], stat_type=stat_type, summary_level="week")
        if df.empty or "pfr_player_id" not in df:
            return
        df = df.copy()
        df["player_id"] = df["pfr_player_id"].map(reg.pfr_to_gsis)
        df = df.dropna(subset=["player_id"])
        out = pd.DataFrame({"player_id": df["player_id"], "week": df["week"]})
        for target, fn in mapping.items():
            out[target] = fn(df)
        out = out.groupby(["player_id", "week"], as_index=False).sum()
        nonlocal ps
        ps = ps.merge(out, on=["player_id", "week"], how="left", suffixes=("", "_pfr"))
        for target in mapping:
            ps[target] = ps[target].fillna(0) + ps.pop(f"{target}_pfr").fillna(0)

    pfr("rush", {"broken_tkl": lambda d: c(d, "rushing_broken_tackles")})
    pfr("rec", {"drops": lambda d: c(d, "receiving_drop")})
    pfr("def", {
        "pressures": lambda d: c(d, "def_pressures") if "def_pressures" in d else
        c(d, "def_times_hurried") + c(d, "def_times_hitqb") + c(d, "def_sacks"),
        "def_targets": lambda d: c(d, "def_targets"),
        "def_cmp_allowed": lambda d: c(d, "def_completions_allowed"),
        "def_yds_allowed": lambda d: c(d, "def_yards_allowed"),
        "def_td_allowed": lambda d: c(d, "def_receiving_td_allowed"),
    })

    # ---- team targets (for target share)
    ps["team_targets"] = ps.groupby(["team", "week"])["targets"].transform("sum")

    # ---- punting from pbp -> merge per punter/week
    if not pbp.empty:
        pbp = pbp[pbp["season_type"].isin(["REG", "POST"])].copy()
        for t in ("posteam", "defteam", "home_team", "away_team", "penalty_team"):
            if t in pbp:
                pbp[t] = pbp[t].map(norm_team)
        punts = pbp[(c(pbp, "punt_attempt") == 1) & pbp["punter_player_id"].notna()].copy()
        if not punts.empty:
            punts["net"] = c(punts, "kick_distance") - c(punts, "return_yards") - 20 * c(punts, "touchback")
            pu = punts.groupby(["punter_player_id", "week", "posteam"]).agg(
                punts=("net", "size"), net_punt_yds=("net", "sum"),
                name=("punter_player_name", "first")).reset_index()
            existing = set(zip(ps["player_id"], ps["week"]))
            add_rows = []
            for r in pu.itertuples(index=False):
                key = (r.punter_player_id, r.week)
                if key in existing:
                    m = (ps["player_id"] == key[0]) & (ps["week"] == key[1])
                    ps.loc[m, "punts"] += r.punts
                    ps.loc[m, "net_punt_yds"] += r.net_punt_yds
                else:
                    row = {col: 0.0 for col in SUM_COLS}
                    row.update(player_id=r.punter_player_id, week=r.week, team=r.posteam,
                               position="P", position_group="SPEC", punts=r.punts,
                               net_punt_yds=r.net_punt_yds, **{name_col: r.name})
                    add_rows.append(row)
            if add_rows:
                ps = pd.concat([ps, pd.DataFrame(add_rows)], ignore_index=True)

    # ---- registry
    for r in ps.drop_duplicates("player_id", keep="last").itertuples(index=False):
        reg.add(r.player_id, getattr(r, name_col), r.position, r.team, getattr(r, "headshot_url", None))

    # ---- schedules (finals only)
    sched = sched[sched["game_type"].isin(["REG", "WC", "DIV", "CON", "SB"])].copy()
    sched["home_team"] = sched["home_team"].map(norm_team)
    sched["away_team"] = sched["away_team"].map(norm_team)
    finals = sched.dropna(subset=["home_score", "away_score"]).copy()
    finals["season_type"] = finals["game_type"].map(lambda g: "REG" if g == "REG" else "POST")

    # OL penalty lookup: position of penalised player
    pos_lookup = dict(zip(ps["player_id"], ps["position"]))
    return dict(ps=ps, pbp=pbp, finals=finals, pos_lookup=pos_lookup)


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------
def player_frame(ps: pd.DataFrame) -> pd.DataFrame:
    g = ps.groupby("player_id")
    df = g[SUM_COLS].sum()
    df["team"] = g["team"].last()
    df["position_group"] = g["position_group"].last()
    df["games"] = g["week"].nunique()

    df["pass_yds"] = df.passing_yards
    df["pass_td"] = df.passing_tds
    df["cmp_pct"] = div(df.completions, df.attempts) * 100
    df["rating"] = passer_rating(df.completions, df.attempts, df.passing_yards,
                                 df.passing_tds, df.passing_interceptions)
    df["ints"] = df.passing_interceptions
    df["sacked"] = df.sacks_suffered
    df["air_yds"] = df.passing_air_yards
    df["pass_epa_play"] = div(df.passing_epa, df.attempts + df.sacks_suffered)
    df["rush_yds"] = df.rushing_yards
    df["rush_td"] = df.rushing_tds
    df["ypc"] = div(df.rushing_yards, df.carries)
    df["fum_lost"] = df.rushing_fumbles_lost + df.receiving_fumbles_lost + df.sack_fumbles_lost
    df["rush_epa_play"] = div(df.rushing_epa, df.carries)
    df["rec_yds"] = df.receiving_yards
    df["rec"] = df.receptions
    df["rec_td"] = df.receiving_tds
    df["yac"] = df.receiving_yards_after_catch
    df["tgt_share"] = div(df.targets, df.team_targets) * 100
    assists = df.def_tackle_assists.where(df.def_tackle_assists > 0, df.def_tackles_with_assist)
    df["tackles"] = df.def_tackles_solo + assists
    df["solo"] = df.def_tackles_solo
    df["tfl"] = df.def_tackles_for_loss
    df["sacks"] = df.def_sacks
    df["qb_hits"] = df.def_qb_hits
    df["ff"] = df.def_fumbles_forced
    df["pbu"] = df.def_pass_defended
    df["def_int"] = df.def_interceptions
    df["cmp_allowed"] = div(df.def_cmp_allowed, df.def_targets) * 100
    df["rating_allowed"] = passer_rating(df.def_cmp_allowed, df.def_targets, df.def_yds_allowed,
                                         df.def_td_allowed, df.def_interceptions)
    df["fg_made_50"] = df.fg_made_50_59 + df.fg_made_60_
    df["fg_att_50"] = df.fg_made_50 + df.fg_missed_50_59 + df.fg_missed_60_
    df["fg_att_u50"] = (df.fg_att - df.fg_att_50).clip(lower=0)
    df["fg_pct"] = div(df.fg_made, df.fg_att) * 100
    df["fg_pct_50"] = div(df.fg_made_50, df.fg_att_50) * 100
    df["fg_pct_u50"] = div(df.fg_made - df.fg_made_50, df.fg_att_u50) * 100
    df["pr_avg"] = div(df.punt_return_yards, df.punt_returns)
    df["kr_avg"] = div(df.kickoff_return_yards, df.kickoff_returns)
    df["net_punt"] = div(df.net_punt_yds, df.punts)
    return df


def drives_frame(pbp: pd.DataFrame) -> pd.DataFrame:
    d = pbp.dropna(subset=["drive", "posteam"])
    if d.empty:
        return pd.DataFrame(columns=["game_id", "posteam", "drive", "top", "result", "in20"])
    agg = d.groupby(["game_id", "posteam", "drive"]).agg(
        top=("drive_time_of_possession", "first"),
        result=("fixed_drive_result", "first"),
        in20=("drive_inside20", "max"),
    ).reset_index()
    agg["top"] = agg["top"].map(parse_clock)
    return agg


def team_frame(pbp: pd.DataFrame, finals: pd.DataFrame, pos_lookup: dict) -> pd.DataFrame:
    """Team-level totals for a subset of games."""
    home = finals.rename(columns={"home_team": "team", "home_score": "pf", "away_score": "pa"})
    away = finals.rename(columns={"away_team": "team", "away_score": "pf", "home_score": "pa"})
    long = pd.concat([home[["team", "pf", "pa", "game_id"]], away[["team", "pf", "pa", "game_id"]]])
    t = long.groupby("team").agg(points_for=("pf", "sum"), points_against=("pa", "sum"),
                                 games=("game_id", "nunique"))
    if pbp.empty:
        return t
    pbp = pbp[pbp["game_id"].isin(finals["game_id"])]
    plays = pbp[pbp["play_type"].isin(["pass", "run"])]
    off = plays.groupby("posteam").agg(yds=("yards_gained", "sum"), epa=("epa", "mean"))
    dfn = plays.groupby("defteam").agg(yds=("yards_gained", "sum"), epa=("epa", "mean"))
    t["off_ypg"] = div(off["yds"].reindex(t.index).fillna(0), t.games)
    t["def_ypg"] = div(dfn["yds"].reindex(t.index).fillna(0), t.games)
    t["off_epa"] = off["epa"].reindex(t.index)
    t["def_epa"] = dfn["epa"].reindex(t.index)

    to = pbp.assign(to=c(pbp, "interception") + c(pbp, "fumble_lost"))
    t["giveaways"] = to.groupby("posteam")["to"].sum().reindex(t.index).fillna(0)
    t["takeaways"] = to.groupby("defteam")["to"].sum().reindex(t.index).fillna(0)
    t["to_diff"] = t.takeaways - t.giveaways

    dr = drives_frame(pbp)
    rz = dr[dr["in20"] == 1]
    rz_td = rz.assign(td=(rz["result"] == "Touchdown").astype(int)).groupby("posteam")["td"].agg(["sum", "size"])
    t["rz_td_pct"] = div(rz_td["sum"], rz_td["size"]).reindex(t.index).fillna(0) * 100
    t["top_avg"] = div(dr.groupby("posteam")["top"].sum().reindex(t.index).fillna(0), t.games)

    # OL proxies
    db = pbp[c(pbp, "qb_dropback") == 1]
    pressured = ((c(db, "sack") == 1) | (c(db, "qb_hit") == 1)).astype(int)
    pb = db.assign(p=pressured).groupby("posteam")["p"].agg(["sum", "size"])
    t["pbwr"] = (1 - div(pb["sum"], pb["size"])).reindex(t.index) * 100
    t["sacks_allowed"] = db.groupby("posteam")["sack"].sum().reindex(t.index).fillna(0)
    runs = pbp[(c(pbp, "rush") == 1) & (c(pbp, "qb_scramble") == 0)]
    win = ((c(runs, "yards_gained") >= 4) | (c(runs, "first_down") == 1) | (c(runs, "touchdown") == 1)).astype(int)
    t["rbwr"] = runs.assign(w=win).groupby("posteam")["w"].mean().reindex(t.index) * 100
    pen = pbp[(c(pbp, "penalty") == 1) & c(pbp, "penalty_player_id", None).notna()]
    if not pen.empty:
        ol = pen[pen["penalty_player_id"].map(pos_lookup).isin(["T", "G", "C", "OL", "OT", "OG"])]
        t["ol_penalties"] = ol.groupby("penalty_team").size().reindex(t.index).fillna(0)
    else:
        t["ol_penalties"] = 0
    return t


def leaders(ps: pd.DataFrame, pbp: pd.DataFrame, finals: pd.DataFrame, pos_lookup: dict) -> dict:
    """Top-N entries for every metric over the supplied subset."""
    if ps.empty:
        return {}
    weeks = max(1, ps["week"].nunique())
    pf = player_frame(ps)
    tf = team_frame(pbp, finals, pos_lookup)
    out = {}
    for m in METRICS:
        src = tf if m["entity"] == "team" else pf
        col = m["col"]
        if col not in src.columns:
            continue
        df = src
        if m["pos"]:
            df = df[df["position_group"].isin(m["pos"])]
        if m["min"]:
            mc, thr = m["min"]
            if mc in df.columns:
                df = df[df[mc] >= max(1, math.ceil(thr * weeks))]
        df = df[df[col].notna()]
        if m["sort"] == "desc" and m["fmt"] != "signed":
            df = df[df[col] != 0]
        if df.empty:
            continue
        df = df.sort_values(col, ascending=(m["sort"] == "asc")).head(TOP_N)
        if m["entity"] == "team":
            out[m["key"]] = [[f"T:{idx}", idx, clean(v, m["fmt"])] for idx, v in df[col].items()]
        else:
            out[m["key"]] = [[idx, r.team, clean(r[col], m["fmt"])] for idx, r in df.iterrows()]
    return out


# ---------------------------------------------------------------------------
# Per-game matchups
# ---------------------------------------------------------------------------
def build_games(data: dict) -> list:
    ps, pbp, finals = data["ps"], data["pbp"], data["finals"]
    finals = finals.sort_values(["gameday", "gametime"] if "gametime" in finals else ["gameday"])
    rec: dict[str, list] = {}
    games = []
    drives = drives_frame(pbp) if not pbp.empty else pd.DataFrame()

    for g in finals.itertuples(index=False):
        h, a = g.home_team, g.away_team
        hs, as_ = int(g.home_score), int(g.away_score)
        for team, w in ((h, hs > as_), (a, as_ > hs)):
            r = rec.setdefault(team, [0, 0, 0])
            if hs == as_:
                r[2] += 1
            else:
                r[0 if w else 1] += 1
        fmt_rec = lambda r: f"{r[0]}-{r[1]}" + (f"-{r[2]}" if r[2] else "")

        # quarter-by-quarter
        q = {"home": [], "away": []}
        gp = pbp[pbp["game_id"] == g.game_id] if not pbp.empty else pd.DataFrame()
        if not gp.empty:
            last = gp.sort_values("play_id").groupby("qtr")[["total_home_score", "total_away_score"]].last()
            ph = pa = 0
            for qtr in range(1, int(max(4, last.index.max())) + 1):
                if qtr in last.index:
                    ch, ca = int(last.loc[qtr, "total_home_score"]), int(last.loc[qtr, "total_away_score"])
                else:
                    ch, ca = ph, pa
                q["home"].append(ch - ph)
                q["away"].append(ca - pa)
                ph, pa = ch, ca
            # reconcile final with the official score
            if q["home"]:
                q["home"][-1] += hs - sum(q["home"])
                q["away"][-1] += as_ - sum(q["away"])

        # team box score
        stats = {}
        for side, team in (("home", h), ("away", a)):
            s = {}
            if not gp.empty:
                o = gp[gp["posteam"] == team]
                plays = o[o["play_type"].isin(["pass", "run"])]
                s["yds"] = int(plays["yards_gained"].sum())
                s["pass"] = int(plays.loc[c(plays, "pass") == 1, "yards_gained"].sum())
                s["rush"] = int(plays.loc[c(plays, "rush") == 1, "yards_gained"].sum())
                s["fd"] = int(c(o, "first_down").sum())
                s["to"] = int((c(o, "interception") + c(o, "fumble_lost")).sum())
                pen = gp[(c(gp, "penalty") == 1) & (gp["penalty_team"] == team)]
                s["pen"] = f"{len(pen)}-{int(c(pen, 'penalty_yards').sum())}"
                conv, fail = int(c(o, "third_down_converted").sum()), int(c(o, "third_down_failed").sum())
                s["third"] = f"{conv}/{conv + fail}"
                dt_ = drives[(drives["game_id"] == g.game_id) & (drives["posteam"] == team)] if not drives.empty else []
                s["top"] = int(dt_["top"].sum()) if len(dt_) else 0
                s["epa"] = round(float(plays["epa"].mean()), 3) if len(plays) else None
            stats[side] = s

        # matchup leaders from player weekly rows
        gw = ps[(ps["week"] == g.week) & (ps["team"].isin([h, a]))].copy()

        def top(team, col, fmt_fn):
            t = gw[(gw["team"] == team) & (gw[col] > 0)]
            if t.empty:
                return None
            r = t.sort_values(col, ascending=False).iloc[0]
            return [r["player_id"], fmt_fn(r), float(r[col])]

        def all_players(team, col, fmt_fn):
            t = gw[(gw["team"] == team) & (gw[col] > 0)]
            if t.empty:
                return None
            t = t.sort_values(col, ascending=False)
            return [[r["player_id"], fmt_fn(r), float(r[col])] for _, r in t.iterrows()]

        defs = {
            "passing": ("passing_yards", lambda r: f"{int(r.completions)}/{int(r.attempts)} · {int(r.passing_yards)} YDS · {int(r.passing_tds)} TD · {int(r.passing_interceptions)} INT"),
            "rushing": ("rushing_yards", lambda r: f"{int(r.carries)} CAR · {int(r.rushing_yards)} YDS · {int(r.rushing_tds)} TD"),
            "receiving": ("receiving_yards", lambda r: f"{int(r.receptions)} REC · {int(r.receiving_yards)} YDS · {int(r.receiving_tds)} TD"),
            "tackles": ("_tkl", lambda r: f"{int(r._tkl)} TKL · {int(r.def_tackles_solo)} SOLO · {int(r.def_tackles_for_loss)} TFL"),
            "sacks": ("def_sacks", lambda r: f"{r.def_sacks:g} SACK · {int(r.def_qb_hits)} QB HIT"),
        }
        gw["_tkl"] = gw["def_tackles_solo"] + gw["def_tackle_assists"].where(gw["def_tackle_assists"] > 0, gw["def_tackles_with_assist"])
        gw["_fr"] = c(gw, "fumble_recovery_opp", 0.0)
        gw["_take"] = gw["def_interceptions"] * 10 + gw["_fr"] * 10 + gw["def_fumbles_forced"]

        def fmt_take(r):
            parts = []
            if r["def_interceptions"] > 0:
                parts.append(f"{int(r['def_interceptions'])} INT")
            if r["_fr"] > 0:
                parts.append(f"{int(r['_fr'])} FR")
            if r["def_fumbles_forced"] > 0:
                parts.append(f"{int(r['def_fumbles_forced'])} FF")
            return " · ".join(parts) if parts else "0"

        ldr = {}
        for k, (col, fn) in defs.items():
            ldr[k] = {"home": top(h, col, fn), "away": top(a, col, fn)}
        ldr["turnovers"] = {"home": all_players(h, "_take", fmt_take), "away": all_players(a, "_take", fmt_take)}

        games.append({
            "id": g.game_id, "week": int(g.week), "type": g.season_type, "date": str(g.gameday),
            "home": h, "away": a, "hs": hs, "as": as_, "q": q,
            "hrec": fmt_rec(rec[h]), "arec": fmt_rec(rec[a]),
            "ot": len(q["home"]) > 4, "stats": stats, "leaders": ldr,
            "stadium": getattr(g, "stadium", None),
        })
    return games


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    cur = current_season_guess()
    env = os.environ.get("SEASONS")
    seasons = sorted({int(s) for s in env.split(",")} if env else {cur - 2, cur - 1, cur}, reverse=True)
    log(f"seasons: {seasons}")

    reg = Registry()
    out = {"seasons": {}}
    current_week = None

    for season in seasons:
        data = load_season(season, reg)
        if data is None:
            continue
        ps, pbp, finals, pl = data["ps"], data["pbp"], data["finals"], data["pos_lookup"]
        weeks = sorted(int(w) for w in finals["week"].unique())
        block = {"weeks": weeks, "leaders": {}, "games": []}

        # season-to-date = regular season only
        reg_ps = ps[ps.get("season_type", "REG") == "REG"] if "season_type" in ps else ps
        reg_f = finals[finals["season_type"] == "REG"]
        reg_pbp = pbp[pbp["season_type"] == "REG"] if not pbp.empty else pbp
        block["leaders"]["season"] = leaders(reg_ps, reg_pbp, reg_f, pl)

        for w in weeks:
            wf = finals[finals["week"] == w]
            wp = pbp[pbp["week"] == w] if not pbp.empty else pbp
            block["leaders"][str(w)] = leaders(ps[ps["week"] == w], wp, wf, pl)
            log(f"  week {w}: {len(block['leaders'][str(w)])} boards")

        block["games"] = build_games(data)
        out["seasons"][str(season)] = block
        if season == seasons[0]:
            current_week = weeks[-1] if weeks else None
            rosters = safe_load(nfl.load_rosters_weekly, [season])
            reg.apply_status(rosters, season)

    if not out["seasons"]:
        log("ERROR: no seasons produced any data")
        sys.exit(1)

    out["meta"] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "current_season": int(next(iter(out["seasons"]))),
        "current_week": current_week,
        "seasons": [int(s) for s in out["seasons"]],
        "categories": CATEGORIES,
        "metrics": [{k: v for k, v in m.items() if k not in ("min", "col")} for m in METRICS],
        "source": "nflverse via nflreadpy",
    }
    out["teams"] = {k: {"name": n, "primary": p, "secondary": s, "logo": logo_url(k)}
                    for k, (n, p, s) in TEAMS.items()}
    out["players"] = reg.players

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False, default=lambda o: None)
    log(f"wrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
