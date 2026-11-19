-- ============================================================================
-- KoLogs Voter Insights: Supabase database setup
-- Paste this whole file into Supabase → SQL Editor → New query → Run.
-- Safe to run again: it only creates what is missing and replaces the functions.
-- ============================================================================

-- Partial-match search ("prasad" finds "DURGAPRASAD")
create extension if not exists pg_trgm;

-- ---------------------------------------------------------------------------
-- The voter table. Filled by tools/load_to_supabase.py
-- ---------------------------------------------------------------------------
create table if not exists public.voters (
  id            bigserial primary key,
  serial_no     text,
  epic          text,
  name          text not null,
  relation_type text,                 -- Father / Husband / Mother / Wife
  relative_name text,
  house         text,                 -- kept as text: 12-204/1 must not become a date
  age           int,
  gender        text,                 -- M / F / O
  ac            text not null,        -- assembly constituency, e.g. 40-ANAPARTHY
  part          int  not null,        -- booth (part) number
  section       text,                 -- street / section of the roll
  village       text,
  mandal        text,
  booth         text generated always as (ac || '|' || part::text) stored,
  search_text   text generated always as (
                  lower(coalesce(name, '') || ' ' || coalesce(relative_name, '') || ' ' ||
                        coalesce(epic, '') || ' ' || coalesce(house, ''))) stored
);

create index if not exists voters_search_trgm on public.voters using gin (search_text gin_trgm_ops);
create index if not exists voters_booth_house on public.voters (booth, house);
create index if not exists voters_epic on public.voters (epic);

-- ---------------------------------------------------------------------------
-- Spelling-tolerant search (added in version 2)
-- vfold() writes a name the same way however it was spelt:
--   th=t, sh=s, ch=c, dh=d, bh=b, ph=p, kh=k, gh=g, x=ks, aa=a, ee=i, oo/ou/ow=u,
--   w=v, z=j, y at the end of a word = i, doubled letters once.
--   So Sathyanarayana = Satyanarayana, Venkateshwara = Venkateswara, Laxmi = Lakshmi.
-- ---------------------------------------------------------------------------
create or replace function public.vfold(t text) returns text
language plpgsql immutable parallel safe
as $$
declare
  s text := lower(coalesce(t, ''));
begin
  s := regexp_replace(s, '[^a-z]+', ' ', 'g');            -- letters only
  s := replace(s, 'x', 'ks');                              -- Laxmi = Lakshmi
  s := regexp_replace(s, '([bcdgjkpst])h', '\1', 'g');     -- th=t, sh=s, dh=d, ch=c, bh=b ...
  s := replace(s, 'aa', 'a');                              -- Prasaad = Prasad
  s := replace(s, 'ee', 'i');                              -- Sreenivas = Srinivas
  s := regexp_replace(s, 'oo|ou|ow', 'u', 'g');            -- Chowdary = Choudary
  s := replace(s, 'w', 'v');                               -- Venkateswara = Venkatesvara
  s := replace(s, 'z', 'j');
  s := regexp_replace(s, 'y\M', 'i', 'g');                 -- Reddy = Reddi
  s := regexp_replace(s, '([a-z])\1+', '\1', 'g');         -- Chitturi = Chituri
  return btrim(regexp_replace(s, '\s+', ' ', 'g'));
end;
$$;

-- The old search index is replaced by the ones below (frees space, speeds up the next step)
drop index if exists public.voters_search_trgm;

-- Spelling-tolerant copies of the names, added in ONE table rewrite:
--   fold_name: the voter's name; fold_all: name|relative without spaces ("durga prasad" = "durgaprasad");
--   fold_text: name and relative with spaces (for "similar names")
alter table public.voters
  add column if not exists fold_name text
    generated always as (public.vfold(name)) stored,
  add column if not exists fold_all text
    generated always as (replace(public.vfold(name), ' ', '') || '|' || replace(public.vfold(relative_name), ' ', '')) stored,
  add column if not exists fold_text text
    generated always as (public.vfold(name) || ' ' || public.vfold(relative_name)) stored;

create index if not exists voters_fold_all_trgm on public.voters using gin (fold_all gin_trgm_ops);
create index if not exists voters_fold_text_trgm on public.voters using gin (fold_text gin_trgm_ops);
create index if not exists voters_house_trgm on public.voters using gin (lower(house) gin_trgm_ops);
drop index if exists public.voters_fold_name_trgm;
drop index if exists public.voters_house;

-- ---------------------------------------------------------------------------
-- Lock the table: nobody on the internet can read it directly.
-- The website can only use the two functions below.
-- ---------------------------------------------------------------------------
alter table public.voters enable row level security;
revoke all on public.voters from anon, authenticated;
revoke all on sequence public.voters_id_seq from anon, authenticated;

-- ---------------------------------------------------------------------------
-- search_voters: at most 200 matches, at least 3 characters. Understands:
--   EPIC (any case) · house numbers (12-342, 12-311/1) · names in any word order,
--   joined or split, spelling variants, name + father's/husband's name together,
--   name + age ("durga 35" = aged 34-36) · and similar names when nothing matches exactly.
-- Returns {total, rows, mode}: mode = epic | house | name | fuzzy
-- ---------------------------------------------------------------------------
drop function if exists public.search_voters(text, text, text, text, text, text);
create or replace function public.search_voters(
  q text,
  p_ac text default '', p_mandal text default '', p_village text default '',
  p_booth text default '', p_section text default ''
) returns json
language plpgsql stable security definer
set search_path = public, pg_temp
as $$
declare
  raw     text := left(btrim(coalesce(q, '')), 60);
  qf      text;
  words   text[];
  pats    text[];
  ages    int[];
  mode    text;
  flt     text;
  wh      text;
  score   text;
  ord     text;
  result  json;
  i       int;
begin
  if length(raw) < 3 then
    return json_build_object('total', 0, 'rows', '[]'::json, 'tooShort', true);
  end if;

  flt := format(' and (%L = '''' or v.ac = %L) and (%L = '''' or v.mandal = %L) and (%L = '''' or v.village = %L)'
             || ' and (%L = '''' or v.booth = %L) and (%L = '''' or v.section = %L)',
                coalesce(p_ac, ''), p_ac, coalesce(p_mandal, ''), p_mandal, coalesce(p_village, ''), p_village,
                coalesce(p_booth, ''), p_booth, coalesce(p_section, ''), p_section);
  ord := 'nullif(regexp_replace(serial_no, ''\D'', '''', ''g''), '''')::int nulls last, id';

  if raw ~* '^[a-z]{2,4}\s*[0-9]{5,8}$' then
    -- EPIC number
    mode := 'epic';
    wh := format('v.epic = %L', upper(regexp_replace(raw, '\s', '', 'g')));
    score := '3';
  elsif raw ~ '^[0-9]' or (raw ~ '[0-9]' and raw ~ '[-/]') then
    -- house number
    mode := 'house';
    wh := format('lower(v.house) like %L escape ''\''', '%' || replace(replace(replace(lower(raw), '\', '\\'), '%', '\%'), '_', '\_') || '%');
    score := format('case when lower(v.house) = %L then 3 else 1 end', lower(raw));
  else
    -- name(s), in any order, plus an optional age
    mode := 'name';
    select array_agg(m[1]::int) into ages
      from regexp_matches(raw, '(?:^|\s)(1[89]|[2-9][0-9]|1[01][0-9])(?:\s|$)', 'g') as m;
    qf := vfold(raw);
    select array_agg(w) into words from (
      select w from unnest(string_to_array(qf, ' ')) as w where length(w) >= 2 limit 6) x;
    if words is null then
      return json_build_object('total', 0, 'rows', '[]'::json, 'mode', 'name');
    end if;
    select array_agg('%' || w || '%') into pats from unnest(words) as w;
    wh := 'true';
    for i in 1 .. array_length(pats, 1) loop
      wh := wh || format(' and v.fold_all like %L', pats[i]);
    end loop;
    if ages is not null then
      wh := wh || format(' and v.age between %s and %s', ages[1] - 1, ages[1] + 1);
    end if;
    score := format('case when replace(v.fold_name, '' '', '''') = %L then 3 '
                 || 'when split_part(v.fold_all, ''|'', 1) like all (%L::text[]) then 2 else 1 end',
                    replace(qf, ' ', ''), pats);
  end if;

  execute format($q$
    with hits as (select v.*, (%s)::float as score from voters v where %s %s),
    firsts as (select * from hits order by score desc, ac, part, %s limit 200)
    select json_build_object('mode', %L, 'total', (select count(*) from hits),
      'rows', coalesce((select json_agg(j) from (
        select json_build_object('epic', epic, 'name', name, 'rt', relation_type, 'relative', relative_name,
          'house', house, 'age', age, 'g', gender, 'booth', booth, 'section', section, 'serial', serial_no) as j
        from firsts order by score desc, ac, part, %s) t), '[]'::json))
  $q$, score, wh, flt, ord, mode, ord) into result;

  -- nothing found by name: show similar names (typos, unusual spellings).
  -- Every typed word must closely match a word in the voter's or relative's name.
  if mode = 'name' and (result->>'total')::int = 0 then
    wh := 'true'; score := '0';
    for i in 1 .. array_length(words, 1) loop
      if length(words[i]) >= 3 then
        wh := wh || format(' and %L <%% v.fold_text', words[i]);
        score := score || format(' + word_similarity(%L, v.fold_text)', words[i]);
      end if;
    end loop;
    if wh = 'true' then
      return result;
    end if;
    if ages is not null then
      wh := wh || format(' and v.age between %s and %s', ages[1] - 1, ages[1] + 1);
    end if;
    execute format($q$
      with hits as (select v.*, (%s)::float as score from voters v where %s %s),
      firsts as (select * from hits order by score desc, ac, part, %s limit 200)
      select json_build_object('mode', 'fuzzy', 'total', (select count(*) from hits),
        'rows', coalesce((select json_agg(j) from (
          select json_build_object('epic', epic, 'name', name, 'rt', relation_type, 'relative', relative_name,
            'house', house, 'age', age, 'g', gender, 'booth', booth, 'section', section, 'serial', serial_no) as j
          from firsts order by score desc, ac, part, %s) t), '[]'::json))
    $q$, score, wh, flt, ord, ord) into result;
  end if;
  return result;
end;
$$;

-- ---------------------------------------------------------------------------
-- house_members: the voters of one house in one booth
-- ---------------------------------------------------------------------------
create or replace function public.house_members(p_booth text, p_house text)
returns json
language sql stable security definer
set search_path = public, pg_temp
as $$
  select coalesce(json_agg(json_build_object(
      'epic', epic, 'name', name, 'rt', relation_type, 'relative', relative_name, 'house', house,
      'age', age, 'g', gender, 'booth', booth, 'section', section, 'serial', serial_no)
    order by nullif(regexp_replace(serial_no, '\D', '', 'g'), '')::int nulls last, id), '[]'::json)
  from voters
  where booth = p_booth and house = p_house and coalesce(p_house, '') <> '';
$$;

-- ---------------------------------------------------------------------------
-- ping: a tiny call used by the weekly "keep awake" job
-- ---------------------------------------------------------------------------
create or replace function public.ping() returns json
language sql stable security definer
set search_path = public, pg_temp
as $$ select json_build_object('ok', true, 'voters', (select count(*) from voters)); $$;

-- Only these three functions are open to the website (the "anon" role)
revoke all on function public.search_voters(text, text, text, text, text, text) from public;
revoke all on function public.house_members(text, text) from public;
revoke all on function public.ping() from public;
grant execute on function public.search_voters(text, text, text, text, text, text) to anon, authenticated;
grant execute on function public.house_members(text, text) to anon, authenticated;
grant execute on function public.ping() to anon, authenticated;
