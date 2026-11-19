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
-- Lock the table: nobody on the internet can read it directly.
-- The website can only use the two functions below.
-- ---------------------------------------------------------------------------
alter table public.voters enable row level security;
revoke all on public.voters from anon, authenticated;
revoke all on sequence public.voters_id_seq from anon, authenticated;

-- ---------------------------------------------------------------------------
-- search_voters: at most 200 matches, at least 3 letters
-- ---------------------------------------------------------------------------
create or replace function public.search_voters(
  q text,
  p_ac text default '', p_mandal text default '', p_village text default '',
  p_booth text default '', p_section text default ''
) returns json
language plpgsql stable security definer
set search_path = public, pg_temp
as $$
declare
  term text := lower(btrim(coalesce(q, '')));
  pattern text;
  result json;
begin
  if length(term) < 3 then
    return json_build_object('total', 0, 'rows', '[]'::json, 'tooShort', true);
  end if;
  term := left(term, 60);
  -- treat % and _ typed by visitors as ordinary characters
  pattern := '%' || replace(replace(replace(term, '\', '\\'), '%', '\%'), '_', '\_') || '%';

  with hits as (
    select v.* from voters v
    where v.search_text like pattern
      and (coalesce(p_ac, '') = ''      or v.ac = p_ac)
      and (coalesce(p_mandal, '') = ''  or v.mandal = p_mandal)
      and (coalesce(p_village, '') = '' or v.village = p_village)
      and (coalesce(p_booth, '') = ''   or v.booth = p_booth)
      and (coalesce(p_section, '') = '' or v.section = p_section)
  ),
  firsts as (
    select * from hits
    order by ac, part, nullif(regexp_replace(serial_no, '\D', '', 'g'), '')::int nulls last, id
    limit 200
  )
  select json_build_object(
    'total', (select count(*) from hits),
    'rows', coalesce((select json_agg(json_build_object(
        'epic', epic, 'name', name, 'rt', relation_type, 'relative', relative_name, 'house', house,
        'age', age, 'g', gender, 'booth', booth, 'section', section, 'serial', serial_no))
      from firsts), '[]'::json)
  ) into result;
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
