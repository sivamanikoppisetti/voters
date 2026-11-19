"""
Builds data.json for the website from the voter rows: booth, street, household and
surname summaries (counts only; no voter names). Same rules as the earlier versions.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

UNREADABLE = "(Name not readable)"

COLS = ["serial", "epic", "name", "rt", "relative", "house", "age", "gender",
        "ac", "section", "village", "part", "mandal"]


def clean(df: pd.DataFrame) -> pd.DataFrame:
    for c in COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS].copy()
    for c in ["serial", "epic", "name", "rt", "relative", "house", "gender", "ac", "section", "village", "mandal"]:
        df[c] = df[c].fillna("").astype(str).str.strip()
    df["serial"] = df["serial"].str.replace(r"\.0$", "", regex=True)
    df["age"] = pd.to_numeric(df["age"], errors="coerce").fillna(0).astype(int)
    df["part"] = pd.to_numeric(df["part"], errors="coerce").fillna(0).astype(int)
    df["epic"] = df["epic"].str.upper()
    df["ac"] = df["ac"].replace("", "Constituency")
    df["section"] = df["section"].replace("", "Unknown section")
    g = df["gender"].str.lower()
    df["g"] = np.where(g.str.startswith("f"), "F", np.where(g.str.startswith("m"), "M", "O"))
    df["booth"] = df["ac"] + "|" + df["part"].astype(str)
    # Never drop a voter just because OCR couldn't read the name: keep them if anything identifies them
    unreadable = (df["name"] == "") & ((df["epic"] != "") | (df["relative"] != "") | (df["house"] != ""))
    df.loc[unreadable, "name"] = UNREADABLE
    df = df[df["name"] != ""].reset_index(drop=True)
    return df


# ---------------------------------------------------------------- surnames
SN_STOP = {"RAO", "REDDY", "KUMAR", "SRI", "SREE", "SHRI", "DEVI", "LAKSHMI", "LAXMI", "VENKATA", "NAGA", "SATYA",
           "SATHYA", "DURGA", "BABU", "RAJU", "RAMA", "SAI", "SIVA", "SHIVA", "VEERA", "SURYA", "BALA", "CHANDRA",
           "PRASAD", "MURTHY", "MURTY", "SWAMY", "SWAMI", "NARAYANA", "KRISHNA", "LATE", "MOHAMMED", "MOHAMMAD",
           "SHAIK", "SHEIK", "SHEK", "BEE", "BEGUM", "AMMA", "MMA", "ANJANEYULU", "SATYANARAYANA", "DOOR", "HNO"}


def sn_fold(w: str) -> str:
    """Spelling-tolerant key: Pithani and Pitani give the same key."""
    w = w.lower()
    w = re.sub(r"([bcdgjkpst])h", r"\1", w)
    w = w.replace("aa", "a").replace("ee", "i")
    w = re.sub(r"oo|ou", "u", w)
    w = w.replace("w", "v").replace("z", "j")
    w = re.sub(r"y$", "i", w)
    return re.sub(r"([a-z])\1+", r"\1", w)


def _words(x: str) -> list[str]:
    return [w for w in re.sub(r"[^A-Z ]", " ", str(x or "").upper()).split() if len(w) >= 3 and w not in SN_STOP]


def surname_of(name: str, relative: str) -> str | None:
    """The family name is the word a voter's name shares with their father's or husband's name."""
    nw = _words(name)
    if len(nw) < 2:
        return None
    rk = {sn_fold(w) for w in _words(relative)}
    shared = [w for w in nw if sn_fold(w) in rk]
    if not shared:
        return None
    ends = [w for w in shared if w in (nw[0], nw[-1])]
    return ends[0] if ends else shared[0]


def assign_surnames(df: pd.DataFrame) -> tuple[pd.Series, dict]:
    spellings: dict[str, Counter] = defaultdict(Counter)
    keys = [""] * len(df)
    names, rels = df["name"].tolist(), df["relative"].tolist()
    for i, (n, r) in enumerate(zip(names, rels)):
        s = surname_of(n, r)
        if s:
            k = sn_fold(s)
            keys[i] = k
            spellings[k][s] += 1
    counts = {k: sum(c.values()) for k, c in spellings.items()}
    # second pass: a name that starts or ends with a surname already seen 3+ times
    for i, n in enumerate(names):
        if keys[i]:
            continue
        nw = _words(n)
        if len(nw) < 2:
            continue
        for w in (nw[-1], nw[0]):
            k = sn_fold(w)
            if counts.get(k, 0) >= 3:
                keys[i] = k
                spellings[k][w] += 1
                break
    display = {k: c.most_common(1)[0][0] for k, c in spellings.items()}
    return pd.Series(keys, index=df.index), display


# ---------------------------------------------------------------- summaries
COUNT_COLS = ["voters", "men", "women", "other", "young", "first", "seniors", "old"]


def _flags(df: pd.DataFrame) -> pd.DataFrame:
    a = df["age"]
    f = pd.DataFrame({
        "voters": 1,
        "men": (df["g"] == "M").astype(int),
        "women": (df["g"] == "F").astype(int),
        "other": (df["g"] == "O").astype(int),
        "young": a.between(18, 29).astype(int),
        "first": a.between(18, 21).astype(int),
        "seniors": (a >= 60).astype(int),
        "old": (a >= 75).astype(int),
    }, index=df.index)
    band = np.where(a < 20, 0, np.where(a >= 80, 7, a // 10 - 1))
    slot = band * 2 + (df["g"] == "F").astype(int)
    for i in range(16):
        f[f"b{i}"] = ((slot == i) & (a >= 18)).astype(int)
    return f


def _group(df: pd.DataFrame, flags: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    g = pd.concat([df[keys], flags], axis=1).groupby(keys, sort=False).sum().reset_index()
    houses = df[df["house"] != ""].groupby(keys, sort=False)["house"].nunique().rename("houses")
    g = g.merge(houses, left_on=keys, right_index=True, how="left")
    g["houses"] = g["houses"].fillna(0).astype(int)
    return g


def _records(g: pd.DataFrame, extra: list[str]) -> list[dict]:
    out = []
    bcols = [f"b{i}" for i in range(16)]
    for r in g.itertuples(index=False):
        d = r._asdict()
        rec = {k: d[k] for k in extra}
        rec.update({k: int(d[k]) for k in COUNT_COLS + ["houses"]})
        rec["bands"] = [int(d[c]) for c in bcols]
        out.append(rec)
    return out


def _mode(s: pd.Series) -> str:
    s = s[s != ""]
    return s.value_counts().index[0] if len(s) else ""


def build_summary(df: pd.DataFrame) -> dict:
    flags = _flags(df)

    # booths: one village and mandal per booth (the most common one)
    meta = df.groupby("booth", sort=False).agg(ac=("ac", "first"), part=("part", "first"),
                                               village=("village", _mode), mandal=("mandal", _mode))
    meta["village"] = np.where(meta["village"] == "", "Part " + meta["part"].astype(str), meta["village"])
    meta["mandal"] = meta["mandal"].replace("", "–")
    b = _group(df, flags, ["booth"]).merge(meta, left_on="booth", right_index=True)
    b = b.rename(columns={"booth": "id"}).sort_values(["ac", "part"])
    booths = _records(b, ["id", "ac", "part", "village", "mandal"])
    for x in booths:
        x["part"] = int(x["part"])

    s = _group(df, flags, ["booth", "section"])
    s["id"] = s["booth"] + "|" + s["section"]
    streets = _records(s, ["id", "booth", "section"])

    # households: the 10 largest of each booth, with member details
    h = df[df["house"] != ""]
    sizes = h.groupby(["booth", "house"], sort=False).size().rename("n").reset_index()
    sizes = sizes.sort_values(["booth", "n"], ascending=[True, False]).groupby("booth").head(10)
    top = set(zip(sizes["booth"], sizes["house"]))
    homes = []
    hh = h[[k in top for k in zip(h["booth"], h["house"])]]
    for (booth, house), grp in hh.groupby(["booth", "house"], sort=False):
        homes.append({
            "booth": booth, "house": house, "n": int(len(grp)),
            "sections": " / ".join(dict.fromkeys(grp["section"])),
            "members": [{"name": r.name, "relative": r.relative, "rt": r.rt, "age": int(r.age), "g": r.g,
                         "epic": r.epic, "serial": r.serial} for r in grp.itertuples(index=False)],
        })

    # surnames per booth and street
    sn_rows = []
    named = df[df["sn"] != ""]
    sg = pd.concat([named[["booth", "section", "sn"]], flags.loc[named.index, ["voters", "men", "women"]]], axis=1)
    sg = sg.groupby(["booth", "section", "sn"], sort=False).sum().reset_index()
    sh = named[named["house"] != ""].groupby(["booth", "section", "sn"], sort=False)["house"].nunique().rename("houses")
    sg = sg.merge(sh, left_on=["booth", "section", "sn"], right_index=True, how="left").fillna({"houses": 0})
    for r in sg.itertuples(index=False):
        sn_rows.append([r.booth, r.section, r.sn, int(r.voters), int(r.men), int(r.women), int(r.houses)])

    return {"booths": booths, "streets": streets, "homes": homes,
            "surnames": sn_rows, "total": int(len(df))}



# ---------------------------------------------------------------- compact format for the website
def compact(df: pd.DataFrame) -> dict:
    """data.json in the compact format index.html expects (names listed once, rows refer by number)."""
    df = clean(df)
    df["sn"], display = assign_surnames(df)
    s = build_summary(df)
    booths = s["booths"]
    bi = {b["id"]: i for i, b in enumerate(booths)}
    sections = sorted({x["section"] for x in s["streets"]})
    si = {x: i for i, x in enumerate(sections)}
    keys = sorted(display)
    ki = {k: i for i, k in enumerate(keys)}
    counts = ["voters", "men", "women", "other", "young", "first", "seniors", "old"]
    return {
        "v": 2, "total": s["total"], "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "booths": booths, "sections": sections,
        "streets": [[bi[x["booth"]], si[x["section"]]] + [x[c] for c in counts] + [x["houses"]] + x["bands"] for x in s["streets"]],
        "homes": [[bi[h["booth"]], h["house"], h["n"], h["sections"]] for h in s["homes"]],
        "surnames": [[bi[r[0]], si[r[1]], ki[r[2]]] + r[3:] for r in s["surnames"]],
        "snNames": [display[k] for k in keys],
    }
