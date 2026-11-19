# Voter Insights by KoLogs

Bilingual (English / తెలుగు) website showing the electoral roll by constituency,
mandal, village, booth, street and household, with a fast voter search.

- **Website:** this repository, served free by GitHub Pages at vote.kologs.com.
- **Charts:** `data.json` (counts only, no voter names), made by the loader.
- **Search and house members:** a free Supabase (Postgres) database. The voter table is
  locked; the website can only call `search_voters` (max 200 results) and `house_members`.
- **No passwords in this repository.** The database password lives only in `tools/.env`
  on the owner's laptop (ignored by Git).

| File | What it is |
|---|---|
| `index.html` | The whole website: layout, colours, charts, English and Telugu text |
| `config.js` | Supabase project address and publishable key (public by design) |
| `data.json` | Chart data, rebuilt by the loader |
| `supabase/schema.sql` | Database setup: table, search index, locks, functions |
| `tools/load_to_supabase.py` | Loads the CSV into Supabase and writes `data.json` |
| `.github/workflows/keep-alive.yml` | Pings the database every 3 days so it doesn't pause |
| `SETUP-SUPABASE.md` | Step-by-step setup and day-to-day guide |
