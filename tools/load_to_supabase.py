"""
Load the electoral roll into Supabase, then write data.json (the website's charts file).

Usage (from the project folder):
  python tools\\load_to_supabase.py C:\\rolls\\all_electoral_rolls.csv --replace
      first time, or to replace everything
  python tools\\load_to_supabase.py C:\\mandapeta\\all_electoral_rolls.csv
      add a constituency (keeps what is already there)
  python tools\\load_to_supabase.py C:\\rolls\\all_electoral_rolls.csv --replace-constituency
      re-load one constituency (deletes only its old rows first)
  python tools\\load_to_supabase.py --refresh-only
      after editing rows in Supabase's Table Editor: just rebuild data.json

Settings: tools\\.env  (copy tools\\.env.example)  →  DATABASE_URL=postgresql://...
After it finishes, push data.json to GitHub so the website shows the new numbers.
"""
import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import psycopg

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import site_data  # noqa: E402

CSV_MAP = {
    "Serial No": "serial", "EPIC Number": "epic", "Name": "name", "Relation Type": "rt",
    "Relative Name": "relative", "House Number": "house", "Age": "age", "Gender": "gender",
    "Assembly Constituency": "ac", "Section": "section", "Village": "village",
    "Part Number": "part", "Tehsil (Mandal)": "mandal",
}
NEEDED = ["Name", "Age", "Gender", "Assembly Constituency", "Part Number"]
DB_COLS = ["serial_no", "epic", "name", "relation_type", "relative_name", "house", "age", "gender",
           "ac", "part", "section", "village", "mandal"]


def read_env():
    """Read tools/.env (KEY=value lines) without extra packages."""
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    url = os.environ.get("DATABASE_URL", "")
    if not url or "PASSWORD" in url:
        sys.exit("Set DATABASE_URL in tools\\.env (Supabase → Connect → Session pooler). See SETUP-SUPABASE.md.")
    return url


def read_csvs(paths):
    frames = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            sys.exit(f"File not found: {p}")
        df = pd.read_csv(p, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
        missing = [c for c in NEEDED if c not in df.columns]
        if missing:
            sys.exit(f"{p.name} is missing these columns: {', '.join(missing)}. Use the extractor's all_electoral_rolls.csv.")
        dates = df.get("House Number", pd.Series(dtype=str)).str.fullmatch(r"\d{1,2}-\d{1,2}-\d{4}").sum()
        if dates > 50:
            print(f"Warning: {dates} house numbers in {p.name} look like dates. Was it re-saved from Excel?")
        df = df[[c for c in CSV_MAP if c in df.columns]].rename(columns=CSV_MAP)
        df = df[~((df["name"] == "Name") & (df["age"] == "Age"))]   # repeated heading rows
        print(f"{p.name}: {len(df):,} rows")
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the rows and give every voter their booth's village and mandal (so filters match the charts)."""
    d = site_data.clean(df)
    mode = lambda s: (s[s != ""].value_counts().index[0] if (s != "").any() else "")
    vil = d.groupby("booth")["village"].agg(mode)
    man = d.groupby("booth")["mandal"].agg(mode)
    d["village"] = d["booth"].map(vil)
    d["village"] = d["village"].where(d["village"] != "", "Part " + d["part"].astype(str))
    d["mandal"] = d["booth"].map(man).replace("", "–")
    return d


def copy_rows(cur, d: pd.DataFrame):
    out = pd.DataFrame({
        "serial_no": d["serial"], "epic": d["epic"], "name": d["name"], "relation_type": d["rt"],
        "relative_name": d["relative"], "house": d["house"], "age": d["age"].astype(int), "gender": d["g"],
        "ac": d["ac"], "part": d["part"].astype(int), "section": d["section"], "village": d["village"],
        "mandal": d["mandal"],
    })
    buf = io.StringIO()
    out.to_csv(buf, index=False, header=False)
    buf.seek(0)
    with cur.copy(f"COPY public.voters ({', '.join(DB_COLS)}) FROM STDIN WITH (FORMAT csv)") as cp:
        while chunk := buf.read(1 << 20):
            cp.write(chunk)


def build_site_data(conn, out_path: Path):
    print("Building data.json from the database …")
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(DB_COLS)} FROM public.voters ORDER BY ac, part, id")
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["serial", "epic", "name", "rt", "relative", "house", "age", "gender",
                                     "ac", "part", "section", "village", "mandal"]).astype(str)
    data = site_data.compact(df)
    out_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {out_path} ({out_path.stat().st_size / 1024:.0f} KB) for {data['total']:,} voters, "
          f"{len(data['booths'])} booths.")


def main():
    ap = argparse.ArgumentParser(description="Load voters into Supabase and rebuild data.json")
    ap.add_argument("csv", nargs="*", help="all_electoral_rolls.csv file(s)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--replace", action="store_true", help="delete all voters first")
    mode.add_argument("--replace-constituency", action="store_true", help="delete only the constituencies in these CSVs first")
    mode.add_argument("--refresh-only", action="store_true", help="don't load anything; just rebuild data.json")
    ap.add_argument("--out", default=str(ROOT / "data.json"), help="where to write data.json")
    args = ap.parse_args()
    if not args.refresh_only and not args.csv:
        ap.error("give the CSV file(s), or use --refresh-only")

    url = read_env()
    t0 = time.time()
    with psycopg.connect(url, prepare_threshold=None) as conn:
        if not args.refresh_only:
            d = prepare(read_csvs(args.csv))
            with conn.cursor() as cur:
                if not (args.replace or args.replace_constituency):
                    cur.execute("SELECT DISTINCT ac FROM public.voters WHERE ac = ANY(%s)", (sorted(d["ac"].unique()),))
                    already = [r[0] for r in cur.fetchall()]
                    if already:
                        sys.exit(f"{', '.join(already)} is already in the database. To load it again without duplicates, "
                                 "add --replace-constituency (or --replace to start fresh).")
                if args.replace:
                    cur.execute("TRUNCATE public.voters RESTART IDENTITY")
                    print("Removed all old voters.")
                elif args.replace_constituency:
                    acs = sorted(d["ac"].unique())
                    cur.execute("DELETE FROM public.voters WHERE ac = ANY(%s)", (acs,))
                    print(f"Removed {cur.rowcount:,} old rows for: {', '.join(acs)}")
                print(f"Uploading {len(d):,} voters …")
                copy_rows(cur, d)
                cur.execute("SELECT count(*) FROM public.voters")
                print(f"The voters table now has {cur.fetchone()[0]:,} rows.")
            conn.commit()
        build_site_data(conn, Path(args.out))
    print(f"Done in {time.time() - t0:.0f} s. Now push data.json to GitHub (see SETUP-SUPABASE.md, step 5).")


if __name__ == "__main__":
    main()
