-- MUSE import processing
--
-- Flow (driven by the Python importer):
--   1. insert into import_batches (status 'staging')
--   2. bulk COPY cleaned rows into import_staging
--   3. select process_import_batch(batch_id)
-- Step 3 creates any missing artists / albums / tracks, inserts the listening
-- events (skipping ones already stored), records stats and clears staging.
-- It runs in one transaction: it either all happens or none of it does.

create or replace function public.process_import_batch(p_batch uuid)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_user          uuid;
  v_status        text;
  v_staged        integer;
  v_new_artists   integer;
  v_new_albums    integer;
  v_new_tracks    integer;
  v_inserted      integer;
  v_stats         jsonb;
begin
  select user_id, status into v_user, v_status
  from import_batches where id = p_batch
  for update;

  if not found then
    raise exception 'import batch % not found', p_batch;
  end if;
  if v_status <> 'staging' then
    raise exception 'import batch % has status %, expected staging', p_batch, v_status;
  end if;

  -- Shared metadata is created here, so run one import at a time.
  perform pg_advisory_xact_lock(hashtext('muse.process_import_batch'));

  select count(*) into v_staged from import_staging where batch_id = p_batch;

  -- 1. Artists (primary and featured) not seen before.
  with raw_names as (
    select artist_name as name from import_staging where batch_id = p_batch
    union all
    select unnest(featured_artists) from import_staging where batch_id = p_batch
  ),
  names as (
    select distinct on (name_key(name)) btrim(name) as name, name_key(name) as k
    from raw_names
    where btrim(coalesce(name, '')) <> ''
    order by name_key(name), name
  ),
  created as (
    insert into artists (name)
    select n.name from names n
    where not exists (select 1 from artist_name_map m where m.name_key = n.k)
    returning id, name_key
  )
  insert into artist_name_map (name_key, artist_id)
  select name_key, id from created;
  get diagnostics v_new_artists = row_count;

  -- 2. Albums, keyed by (artist, title).
  -- Note: Spotify's export only names the track's main artist, so a
  -- various-artists compilation becomes one album per artist until enriched.
  insert into albums (title, primary_artist_id)
  select distinct on (m.artist_id, name_key(s.album_name))
         btrim(s.album_name), m.artist_id
  from import_staging s
  join artist_name_map m on m.name_key = name_key(s.artist_name)
  where s.batch_id = p_batch
    and btrim(coalesce(s.album_name, '')) <> ''
  order by m.artist_id, name_key(s.album_name), s.album_name
  on conflict (primary_artist_id, title_key) do nothing;
  get diagnostics v_new_albums = row_count;

  -- 3. Tracks, keyed by Spotify URI. If Spotify renamed a track over time,
  -- the most recent name wins.
  insert into tracks (title, album_id, primary_artist_id, spotify_uri, version_type)
  select distinct on (s.spotify_track_uri)
         btrim(s.track_name), al.id, m.artist_id, s.spotify_track_uri, s.version_type
  from import_staging s
  join artist_name_map m on m.name_key = name_key(s.artist_name)
  left join albums al on al.primary_artist_id = m.artist_id
                     and al.title_key = name_key(s.album_name)
  where s.batch_id = p_batch
  order by s.spotify_track_uri, s.ended_at desc
  on conflict (spotify_uri) do nothing;
  get diagnostics v_new_tracks = row_count;

  -- 4. Track credits: primary artist, plus featured artists parsed from titles.
  insert into track_artists (track_id, artist_id, role, position)
  select t.id, t.primary_artist_id, 'primary', 0
  from tracks t
  where t.spotify_uri in (select spotify_track_uri from import_staging where batch_id = p_batch)
  on conflict do nothing;

  insert into track_artists (track_id, artist_id, role, position, source)
  select distinct on (t.id, m.artist_id) t.id, m.artist_id, 'featured', f.ord, 'title_parse'
  from import_staging s
  cross join lateral unnest(s.featured_artists) with ordinality as f(name, ord)
  join tracks t on t.spotify_uri = s.spotify_track_uri
  join artist_name_map m on m.name_key = name_key(f.name)
  where s.batch_id = p_batch
    and m.artist_id <> t.primary_artist_id
  order by t.id, m.artist_id, f.ord
  on conflict do nothing;

  -- 5. Listening events. Anything already stored (same track, same stop
  -- time) is skipped, so overlapping exports can be imported safely.
  insert into listening_events (
    user_id, track_id, started_at, ended_at, ms_played,
    skipped, shuffle, offline, incognito, reason_start, reason_end, import_batch_id
  )
  select v_user, t.id,
         s.ended_at - make_interval(secs => s.ms_played / 1000.0),
         s.ended_at, s.ms_played,
         s.skipped, s.shuffle, s.offline, s.incognito, s.reason_start, s.reason_end, p_batch
  from import_staging s
  join tracks t on t.spotify_uri = s.spotify_track_uri
  where s.batch_id = p_batch
  on conflict (user_id, ended_at, track_id) do nothing;
  get diagnostics v_inserted = row_count;

  v_stats := jsonb_build_object(
    'staged_rows',        v_staged,
    'events_inserted',    v_inserted,
    'already_stored',     v_staged - v_inserted,
    'new_artists',        v_new_artists,
    'new_albums',         v_new_albums,
    'new_tracks',         v_new_tracks
  );

  delete from import_staging where batch_id = p_batch;

  update import_batches
  set status = 'complete',
      stats = stats || v_stats,
      completed_at = now()
  where id = p_batch;

  return v_stats;
end;
$$;

-- Removes the listening events an import added. Artists, albums and tracks
-- stay, since other imports or users may share them.
-- If a later import overlapped this one, the overlapping plays were credited
-- to this batch and are removed too; re-import the later file to restore them.
create or replace function public.rollback_import_batch(p_batch uuid)
returns integer
language plpgsql
set search_path = public
as $$
declare
  v_deleted integer;
begin
  perform 1 from import_batches where id = p_batch for update;
  if not found then
    raise exception 'import batch % not found', p_batch;
  end if;

  delete from listening_events where import_batch_id = p_batch;
  get diagnostics v_deleted = row_count;
  delete from import_staging where batch_id = p_batch;

  update import_batches
  set status = 'rolled_back',
      stats = stats || jsonb_build_object('rolled_back_events', v_deleted),
      completed_at = now()
  where id = p_batch;

  return v_deleted;
end;
$$;

-- Only the server (service role / database owner) may run imports.
revoke execute on function public.process_import_batch(uuid)  from public, anon, authenticated;
revoke execute on function public.rollback_import_batch(uuid) from public, anon, authenticated;
