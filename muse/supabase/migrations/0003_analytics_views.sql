-- MUSE analytics views
-- security_invoker makes the views respect row level security, so a signed-in
-- user only ever sees their own plays through them.

-- One row per counted play: long enough to pass the user's min_play_ms
-- threshold (30 seconds by default), with names joined in and a local time.
create or replace view public.plays
with (security_invoker = true) as
select
  e.id,
  e.user_id,
  e.track_id,
  t.title                              as track_title,
  t.version_type,
  t.primary_artist_id                  as artist_id,
  a.name                               as artist_name,
  t.album_id,
  al.title                             as album_title,
  e.started_at,
  e.ended_at,
  (e.started_at at time zone p.timezone) as started_local,
  e.ms_played,
  e.skipped,
  e.shuffle,
  e.offline,
  e.incognito
from public.listening_events e
join public.profiles p on p.id = e.user_id
join public.tracks   t on t.id = e.track_id
join public.artists  a on a.id = t.primary_artist_id
left join public.albums al on al.id = t.album_id
where e.ms_played >= p.min_play_ms;

-- Monthly totals per artist: the building block for the Timeline and for
-- loyalty / evolution metrics. Becomes a precalculated table once the
-- history is large enough for this to feel slow.
create or replace view public.monthly_artist_stats
with (security_invoker = true) as
select
  user_id,
  date_trunc('month', started_local)::date as month,
  artist_id,
  artist_name,
  count(*)                                 as plays,
  sum(ms_played)                           as ms_played,
  count(distinct track_id)                 as distinct_tracks
from public.plays
group by user_id, date_trunc('month', started_local)::date, artist_id, artist_name;
