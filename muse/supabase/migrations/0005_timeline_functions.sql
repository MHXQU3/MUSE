-- MUSE: Timeline page data
--
-- Two functions the web app calls (through Supabase RPC) as the signed-in
-- user. They run with that user's permissions (security invoker) and read
-- through the `plays` view, so row level security keeps everyone to their
-- own history. Times are in the user's own timezone (profiles.timezone).

-- The whole history, month by month: drives the shelf at the top of the page.
create or replace function public.timeline_overview()
returns jsonb
language sql
stable
security invoker
set search_path = public
as $$
  with p as (
    select date_trunc('month', pl.started_local)::date as month,
           pl.ms_played, pl.artist_id, coalesce(t.language, 'und') as language
    from plays pl join tracks t on t.id = pl.track_id
    where pl.user_id = auth.uid()
  ), months as (
    select month, sum(ms_played) as ms, count(*) as plays, count(distinct artist_id) as artists
    from p group by month
  ), langs as (
    select month, jsonb_object_agg(language, ms order by ms desc) as languages
    from (select month, language, sum(ms_played) as ms from p group by month, language) x
    group by month
  ), top_artist as (
    select distinct on (month) month, a.name
    from (select month, artist_id, sum(ms_played) as ms from p group by month, artist_id) x
    join artists a on a.id = x.artist_id
    order by month, ms desc
  )
  select jsonb_build_object(
    'timezone', (select timezone from profiles where id = auth.uid()),
    'first_play', (select min(started_local) from plays where user_id = auth.uid()),
    'last_play',  (select max(started_local) from plays where user_id = auth.uid()),
    'months', coalesce((
      select jsonb_agg(jsonb_build_object(
               'month', to_char(m.month, 'YYYY-MM'), 'ms', m.ms, 'plays', m.plays,
               'artists', m.artists, 'languages', l.languages, 'top_artist', ta.name)
             order by m.month)
      from months m join langs l using (month) join top_artist ta using (month)), '[]')
  )
$$;

-- Everything about one stretch of time.
-- p = {"start": "2024-10-01", "end": "2024-11-01", "bucket": "day"|"month"|"hour"}
-- start is inclusive, end exclusive, both local dates.
create or replace function public.timeline_period(p jsonb)
returns jsonb
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  v_uid    uuid := auth.uid();
  v_start  timestamp := (p->>'start')::date;
  v_end    timestamp := (p->>'end')::date;
  v_bucket text := coalesce(p->>'bucket', 'day');
  v_out    jsonb;
begin
  if v_uid is null then
    raise exception 'not signed in';
  end if;
  if v_bucket not in ('hour', 'day', 'month') then
    raise exception 'bucket must be hour, day or month';
  end if;
  if v_end <= v_start then
    raise exception 'end must be after start';
  end if;

  with pp as (
    select pl.*, coalesce(t.language, 'und') as language, a.country
    from plays pl
    join tracks t on t.id = pl.track_id
    join artists a on a.id = pl.artist_id
    where pl.user_id = v_uid and pl.started_local >= v_start and pl.started_local < v_end
  ),
  totals as (
    select coalesce(sum(ms_played), 0) as ms, count(*) as plays,
           count(distinct artist_id) as artists, count(distinct track_id) as tracks
    from pp
  ),
  first_heard as (
    -- When each artist in this period was first ever played.
    select pl.artist_id, min(pl.started_local) as first_at
    from plays pl
    where pl.user_id = v_uid and pl.artist_id in (select distinct artist_id from pp)
    group by pl.artist_id
  ),
  artist_lang as (
    select distinct on (artist_id) artist_id, language
    from (select artist_id, language, sum(ms_played) ms from pp group by 1, 2) x
    order by artist_id, ms desc
  ),
  top_artists as (
    select pp.artist_id, pp.artist_name, max(pp.country) as country, sum(pp.ms_played) as ms, count(*) as plays
    from pp group by pp.artist_id, pp.artist_name
    order by ms desc limit 10
  ),
  top_tracks as (
    select pp.track_id, pp.track_title, pp.artist_name, max(pp.language) as language,
           sum(pp.ms_played) as ms, count(*) as plays
    from pp group by pp.track_id, pp.track_title, pp.artist_name
    order by plays desc, ms desc limit 10
  ),
  top_albums as (
    select pp.album_id, pp.album_title, pp.artist_name, sum(pp.ms_played) as ms,
           max(al.cover_url) as cover_url, max(al.release_date)::text as release_date,
           mode() within group (order by pp.language) as language
    from pp join albums al on al.id = pp.album_id
    group by pp.album_id, pp.album_title, pp.artist_name
    order by ms desc limit 6
  ),
  discoveries as (
    select fh.artist_id, a.name, to_char(fh.first_at, 'YYYY-MM-DD"T"HH24:MI') as first_at,
           (select pp.track_title from pp where pp.artist_id = fh.artist_id
            order by pp.started_local limit 1) as first_track,
           (select sum(pp.ms_played) from pp where pp.artist_id = fh.artist_id) as ms
    from first_heard fh join artists a on a.id = fh.artist_id
    where fh.first_at >= v_start and fh.first_at < v_end
    order by ms desc limit 8
  ),
  buckets as (
    select case v_bucket
             when 'hour'  then to_char(started_local, 'HH24')
             when 'day'   then to_char(started_local, 'YYYY-MM-DD')
             else              to_char(started_local, 'YYYY-MM') end as key,
           sum(ms_played) as ms, count(*) as plays
    from pp group by 1
  ),
  languages as (
    select language, sum(ms_played) as ms from pp group by language
  )
  select jsonb_build_object(
    'start', p->>'start', 'end', p->>'end', 'bucket', v_bucket,
    'totals', (select jsonb_build_object('ms', ms, 'plays', plays, 'artists', artists, 'tracks', tracks,
                                         'new_artists', (select count(*) from first_heard
                                                         where first_at >= v_start and first_at < v_end))
               from totals),
    'top_artists', coalesce((select jsonb_agg(jsonb_build_object(
        'id', ta.artist_id, 'name', ta.artist_name, 'country', ta.country, 'ms', ta.ms, 'plays', ta.plays,
        'language', al.language,
        'is_new', fh.first_at >= v_start) order by ta.ms desc)
      from top_artists ta join first_heard fh using (artist_id) left join artist_lang al using (artist_id)), '[]'),
    'top_tracks', coalesce((select jsonb_agg(jsonb_build_object(
        'id', track_id, 'title', track_title, 'artist', artist_name, 'language', language,
        'ms', ms, 'plays', plays) order by plays desc, ms desc) from top_tracks), '[]'),
    'top_albums', coalesce((select jsonb_agg(jsonb_build_object(
        'id', album_id, 'title', album_title, 'artist', artist_name, 'ms', ms,
        'cover_url', cover_url, 'release_date', release_date, 'language', language) order by ms desc)
      from top_albums), '[]'),
    'discoveries', coalesce((select jsonb_agg(jsonb_build_object(
        'id', artist_id, 'name', name, 'first_at', first_at, 'first_track', first_track, 'ms', ms)
        order by ms desc) from discoveries), '[]'),
    'buckets', coalesce((select jsonb_agg(jsonb_build_object('key', key, 'ms', ms, 'plays', plays)
                                          order by key) from buckets), '[]'),
    'languages', coalesce((select jsonb_object_agg(language, ms order by ms desc) from languages), '{}'),
    -- The play-by-play log, only for a single day.
    'log', case when v_end - v_start <= interval '1 day' then coalesce((
        select jsonb_agg(jsonb_build_object(
          'at', to_char(e.started_at at time zone pr.timezone, 'HH24:MI'),
          'title', t.title, 'artist', a.name, 'ms_played', e.ms_played,
          'skipped', coalesce(e.skipped, false), 'counted', e.ms_played >= pr.min_play_ms,
          'language', t.language)
          order by e.started_at)
        from listening_events e
        join profiles pr on pr.id = e.user_id
        join tracks t on t.id = e.track_id
        join artists a on a.id = t.primary_artist_id
        where e.user_id = v_uid
          and (e.started_at at time zone pr.timezone) >= v_start
          and (e.started_at at time zone pr.timezone) < v_end), '[]') end
  ) into v_out;

  return v_out;
end;
$$;

revoke execute on function public.timeline_overview()      from public, anon;
revoke execute on function public.timeline_period(jsonb)   from public, anon;
grant  execute on function public.timeline_overview()      to authenticated;
grant  execute on function public.timeline_period(jsonb)   to authenticated;
