-- MUSE: first insight queries
-- Each one is a prototype for a screen in the app. They read from the
-- `plays` view (counted plays only, local time), so they work for any user
-- and respect row level security when run as that user.
--
-- Run as the database owner with:  psql -d <db> -v email=you@example.com -f queries/insights.sql

\set user_id_sql 'select id from auth.users where lower(email) = lower(' :'email' ')'
select (:user_id_sql) as uid \gset

\echo '\n== 1. Overview (homepage header)'
select count(*)                                      as plays,
       round(sum(ms_played) / 3.6e6)                 as hours,
       count(distinct artist_id)                     as artists,
       count(distinct track_id)                      as tracks,
       count(distinct album_id)                      as albums,
       min(started_local)::date                      as first_play,
       max(started_local)::date                      as last_play
from public.plays where user_id = :'uid';

\echo '\n== 2. Top 3 artists per year, by listening time (eras)'
select year, rank, artist_name, hours, share_pct
from (
  select year, artist_name,
         round(sum(ms_played) / 3.6e6, 1) as hours,
         round(100.0 * sum(ms_played) / sum(sum(ms_played)) over (partition by year), 1) as share_pct,
         rank() over (partition by year order by sum(ms_played) desc) as rank
  from (select extract(year from started_local)::int as year, artist_name, ms_played
        from public.plays where user_id = :'uid') p
  group by year, artist_name
) t
where rank <= 3
order by year, rank;

\echo '\n== 3. Where the music comes from, share of listening time by artist country'
select year, country,
       round(100.0 * sum(ms_played) / sum(sum(ms_played)) over (partition by year), 1) as share_pct
from (select extract(year from p.started_local)::int as year,
             coalesce(a.country, '??') as country, p.ms_played
      from public.plays p join public.artists a on a.id = p.artist_id
      where p.user_id = :'uid') x
group by year, country
order by year, share_pct desc;

\echo '\n== 4. Musical diversity per year: normalised Shannon entropy over artists'
-- 0 = all listening on one artist, 1 = spread perfectly evenly across every
-- artist played that year. Transparent and explainable, as the blueprint asks.
with shares as (
  select year, artist_id,
         sum(ms_played)::numeric / sum(sum(ms_played)) over (partition by year) as p
  from (select extract(year from started_local)::int as year, artist_id, ms_played
        from public.plays where user_id = :'uid') x
  group by year, artist_id
)
select year,
       count(*)                                            as artists,
       round(-sum(p * ln(p)), 3)                           as entropy,
       round(-sum(p * ln(p)) / nullif(ln(count(*)::numeric), 0), 3) as diversity_0_to_1
from shares where p > 0
group by year order by year;

\echo '\n== 5. Artist loyalty: in how many months since you found them were they a real part of your listening?'
-- "Real part" = at least 2% of that month's listening time. Simply appearing
-- in a month is too easy a bar for anyone you play often.
with monthly as (
  select artist_name, month, ms_played,
         ms_played::numeric / sum(ms_played) over (partition by month) as share
  from public.monthly_artist_stats where user_id = :'uid'
), first_seen as (
  select artist_name, min(month) as first_month from monthly group by 1
), all_months as (
  select distinct month from monthly
)
select f.artist_name, f.first_month,
       count(*) filter (where m.share >= 0.02)                         as strong_months,
       (select count(*) from all_months am where am.month >= f.first_month) as possible_months,
       round(count(*) filter (where m.share >= 0.02)::numeric
             / (select count(*) from all_months am where am.month >= f.first_month), 2) as loyalty
from first_seen f join monthly m using (artist_name)
group by f.artist_name, f.first_month
order by loyalty desc
limit 8;

\echo '\n== 6. Taste evolution: biggest changes in listening share, 2023 vs 2026'
with by_year as (
  select artist_name, extract(year from started_local)::int as year, sum(ms_played) ms
  from public.plays where user_id = :'uid' and extract(year from started_local) in (2023, 2026)
  group by 1, 2
), shares as (
  select artist_name,
         coalesce(100.0 * sum(ms) filter (where year = 2023) / (select sum(ms) from by_year where year = 2023), 0) as share_2023,
         coalesce(100.0 * sum(ms) filter (where year = 2026) / (select sum(ms) from by_year where year = 2026), 0) as share_2026
  from by_year group by 1
)
(select 'rising' as direction, artist_name, round(share_2023, 1) as pct_2023, round(share_2026, 1) as pct_2026,
        round(share_2026 - share_2023, 1) as change
 from shares order by share_2026 - share_2023 desc limit 5)
union all
(select 'fading', artist_name, round(share_2023, 1), round(share_2026, 1), round(share_2026 - share_2023, 1)
 from shares order by share_2026 - share_2023 asc limit 5);

\echo '\n== 7. Time capsule: your world in October 2024'
select artist_name, track_title, count(*) as plays
from public.plays
where user_id = :'uid' and started_local >= '2024-10-01' and started_local < '2024-11-01'
group by 1, 2 order by plays desc limit 5;

\echo '\n== 8. Most skipped artists (min 100 attempts, all events incl. short ones)'
select a.name as artist_name, count(*) as attempts,
       round(100.0 * count(*) filter (where e.skipped) / count(*), 1) as skip_pct
from public.listening_events e
join public.tracks t on t.id = e.track_id
join public.artists a on a.id = t.primary_artist_id
where e.user_id = :'uid'
group by 1 having count(*) >= 100
order by skip_pct desc limit 5;

\echo '\n== 9. Genre distribution per year (needs MusicBrainz enrichment)'
-- An artist's listening is split across their genres in proportion to how
-- strongly MusicBrainz users tagged each one.
with artist_genre_share as (
  select ag.artist_id, g.name as genre, ag.weight / sum(ag.weight) over (partition by ag.artist_id) as w
  from public.artist_genres ag join public.genres g on g.id = ag.genre_id
), per_year as (
  select extract(year from p.started_local)::int as year, ags.genre, sum(p.ms_played * ags.w)::numeric as ms
  from public.plays p join artist_genre_share ags on ags.artist_id = p.artist_id
  where p.user_id = :'uid'
  group by 1, 2
)
select year, genre, share_pct
from (select year, genre, round(100.0 * ms / sum(ms) over (partition by year), 1) as share_pct,
             rank() over (partition by year order by ms desc) as r
      from per_year) x
where r <= 4  -- top 4 genres; shares are of all listening that year
order by year, share_pct desc;

\echo '\n== 10. Language distribution per year (from album lyrics language)'
select year, coalesce(language, '??') as language,
       round(100.0 * sum(ms_played) / sum(sum(ms_played)) over (partition by year), 1) as share_pct
from (select extract(year from p.started_local)::int as year, t.language, p.ms_played
      from public.plays p join public.tracks t on t.id = p.track_id
      where p.user_id = :'uid') x
group by year, language
order by year, share_pct desc;
