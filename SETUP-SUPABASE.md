# Setup: Supabase + GitHub Pages + vote.kologs.com

Free, no card. About 45 minutes the first time.

```
 your CSV ──(tools/load_to_supabase.py)──► Supabase database (locked; fast search)
                         │
                         └──► data.json (charts: counts only, no names) ──► GitHub Pages ──► vote.kologs.com
```

- Charts, tabs and filters come from `data.json`, served by GitHub Pages: fast, unlimited.
- Search and household members come from Supabase: under a second.
- The voter table is locked. The website can only call two functions: search (max 200 results,
  at least 3 letters) and the members of one house.

---

## Step 1: Create the Supabase project

1. Go to <https://supabase.com> → **Start your project** → sign in (GitHub sign-in is easiest).
2. **New project**:
   - Name: `kologs-voters`
   - **Database password**: click *Generate a password* and **save it somewhere safe** (you need it in step 4)
   - Region: **South Asia (Mumbai)**
   - Plan: **Free**
3. **Create new project**. Wait 1–2 minutes until it says the project is ready.

## Step 2: Create the table and functions

1. Left menu → **SQL Editor** → **+ New query**.
2. Open `supabase/schema.sql` from this folder, copy everything, paste it in.
3. Click **Run**. You should see *Success. No rows returned*.
4. Left menu → **Table Editor**: a table **voters** now exists (empty).

## Step 3: Connect the website (config.js)

1. Left menu → **Project Settings** (gear) → **API Keys**. Copy the **Publishable key**
   (starts with `sb_publishable_`). If you only see *anon public* under "Legacy API keys",
   that works too.
2. **Project Settings → Data API** (or the project's home page): copy the **Project URL**,
   like `https://abcdefghijk.supabase.co`.
3. Open `config.js` and paste both:
   ```js
   window.SUPABASE_URL = 'https://abcdefghijk.supabase.co';
   window.SUPABASE_KEY = 'sb_publishable_xxxxxxxxxxxx';
   ```
   These two are meant to be public; they only allow the two website functions.
   **Never** put the *secret* / *service_role* key or the database password in `config.js`.

## Step 4: Load the voters (on your laptop, once per data update)

1. In this folder, copy `tools\.env.example` to **`tools\.env`**.
2. In Supabase click **Connect** (top of the project) → **Connection string** →
   **Session pooler** → copy the URI. Paste it after `DATABASE_URL=` in `tools\.env`, and replace
   `[YOUR-PASSWORD]` with the database password from step 1.
   (Use *Session pooler*, not *Direct connection*: it works on every home internet.)
3. In VS Code, open this folder → **Terminal → New Terminal**:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r tools\requirements.txt
   .\.venv\Scripts\python.exe tools\load_to_supabase.py "C:\rolls\all_electoral_rolls.csv" --replace
   ```
   Use the extractor's original `all_electoral_rolls.csv` (not a copy saved from Excel).
4. It ends with lines like:
   ```
   The voters table now has 203,223 rows.
   Wrote ...\data.json (1170 KB) for 203,223 voters, 247 booths.
   ```
   A new file **`data.json`** is now in this folder.

`tools\.env` holds your database password: it's listed in `.gitignore`, so it never goes to GitHub.
Keep it that way.

## Step 5: Update the GitHub repo (`voters`)

Your existing repo keeps its GitHub Pages and domain settings; you only replace files.

1. Open the repo on github.com → **Add file → Upload files**.
2. Drag in from this folder: `index.html`, `config.js`, **`data.json`**, `kologs-logo.png`,
   `README.md`, `SETUP-SUPABASE.md`, `.gitignore`, and the folders `supabase`, `tools`
   and `.github`.
   - **Do not** upload `tools\.env` or any CSV/Excel file. (If you drag the `tools` folder, check
     the list GitHub shows and remove `.env` if it appears.)
   - Your repo's `CNAME` already says `vote.kologs.com`; don't replace it unless you want another name.
3. **Commit changes**. After about a minute, **https://vote.kologs.com** shows the new version.
4. Optional: delete the old `apps-script` folder and `SETUP.md` from the repo (they're no longer used).

## Step 6: Keep search awake (one time)

Free Supabase projects pause after **7 days without use**. The file
`.github/workflows/keep-alive.yml` makes a tiny request every 3 days.

1. In the repo: **Actions** tab → if asked, **I understand my workflows, go ahead and enable them**.
2. Click **Keep Supabase awake** → **Run workflow** → **Run workflow**. After ~20 seconds it should
   show a green tick.

Note: GitHub switches scheduled jobs off in repos with **no commits for 60 days** and e-mails you.
Uploading a new `data.json` counts as activity; or re-enable it in the Actions tab.
If the database ever pauses anyway: Supabase dashboard → your project → **Restore project**.

---

## Day-to-day

| What you want | What to do |
|---|---|
| Correct one voter | Supabase → **Table Editor → voters** → edit the cell → save. Search shows it at once. Then run `tools\load_to_supabase.py --refresh-only` and upload the new `data.json` if counts changed (e.g. gender, age, village) |
| Add a constituency (e.g. Mandapeta) | `load_to_supabase.py "C:\mandapeta\all_electoral_rolls.csv"` → upload `data.json` |
| Reload a constituency (new roll) | same, with `--replace-constituency` → upload `data.json` |
| Change the website's look or text | Edit `index.html` (colours at the top, wording in `I18N`) → upload it |
| See how much free quota is used | Supabase → **Project Settings → Usage** (database size limit 500 MB; you use about 90 MB per 2 lakh voters) |

## If something's wrong

| What you see | Fix |
|---|---|
| Site: *not connected to its database yet* | Fill in `config.js` (step 3) and upload it |
| Site: *Could not load the voter data* | `data.json` isn't in the repo yet (step 4–5) |
| Search: *starting up after a quiet period* | The project paused: Supabase → **Restore project**, then check step 6 |
| Search: *Search failed* | Check `config.js` values; in Supabase SQL Editor run `select ping();` |
| Loader: `connection ... failed` / timeout | Use the **Session pooler** string, and the right password in `tools\.env` |
| Loader: *… is already in the database* | Add `--replace-constituency` (or `--replace`) |
| Loader: *missing these columns* | Use the extractor's `all_electoral_rolls.csv` |
