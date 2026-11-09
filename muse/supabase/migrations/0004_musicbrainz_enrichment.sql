-- MUSE: MusicBrainz enrichment
--
-- The Python enricher asks the database what to look up next
-- (musicbrainz_queue), queries MusicBrainz, then sends its decisions back in
-- small chunks (apply_musicbrainz_results). Every decision is recorded in
-- metadata_matches, so nothing is lost and any match can be reviewed.
--
-- Match statuses
--   auto       confident match, applied
--   suggested  plausible match, NOT applied until you confirm it
--   unmatched  nothing good enough found
--   confirmed  a suggestion you accepted (applied, locked)
--   rejected   a candidate you said is wrong (never suggested again)
--   manual     an MBID you set yourself (applied, locked)
-- A locked entity is never overwritten by automatic matching.

alter table public.metadata_matches drop constraint metadata_matches_status_check;
alter table public.metadata_matches add constraint metadata_matches_status_check
  check (status in ('auto', 'suggested', 'unmatched', 'confirmed', 'rejected', 'manual'));

alter table public.artists
  add column mb_type             text,      -- Person, Group, ...
  add column disambiguation      text,      -- MusicBrainz's "which one" note
  add column enrich_attempted_at timestamptz;

alter table public.albums
  add column cover_url           text,
  add column enrich_attempted_at timestamptz;

alter table public.tracks
  add column enrich_attempted_at timestamptz;

-- A standard and a deluxe edition are different MUSE albums but belong to the
-- same MusicBrainz release group, so album MBIDs can't be unique.
alter table public.albums drop constraint albums_mbid_key;
create index albums_mbid_idx on public.albums (mbid);
create index tracks_mbid_idx on public.tracks (mbid);

-- ---------------------------------------------------------------------------
-- Merge two artists that turn out to be the same person (e.g. "JAY-Z" and
-- "Jay Z" both match one MusicBrainz artist). Everything moves to p_keep.
-- ---------------------------------------------------------------------------
create or replace function public.merge_artists(p_keep uuid, p_drop uuid)
returns void
language plpgsql
set search_path = public
as $$
begin
  if p_keep = p_drop then
    return;
  end if;

  -- Albums with the same title under both artists become one album.
  create temp table _album_merge on commit drop as
    select d.id as drop_id, k.id as keep_id
    from albums d join albums k on k.primary_artist_id = p_keep and k.title_key = d.title_key
    where d.primary_artist_id = p_drop;

  update tracks t set album_id = m.keep_id from _album_merge m where t.album_id = m.drop_id;
  delete from library_items li using _album_merge m, library_items keep_li
    where li.entity_type = 'album' and li.entity_id = m.drop_id
      and keep_li.user_id = li.user_id and keep_li.entity_type = 'album' and keep_li.entity_id = m.keep_id;
  update library_items li set entity_id = m.keep_id from _album_merge m
    where li.entity_type = 'album' and li.entity_id = m.drop_id;
  delete from collection_items ci using _album_merge m, collection_items keep_ci
    where ci.entity_type = 'album' and ci.entity_id = m.drop_id
      and keep_ci.collection_id = ci.collection_id and keep_ci.entity_type = 'album' and keep_ci.entity_id = m.keep_id;
  update collection_items ci set entity_id = m.keep_id from _album_merge m
    where ci.entity_type = 'album' and ci.entity_id = m.drop_id;
  delete from user_notes un using _album_merge m, user_notes keep_un
    where un.entity_type = 'album' and un.entity_id = m.drop_id
      and keep_un.user_id = un.user_id and keep_un.entity_type = 'album' and keep_un.entity_id = m.keep_id;
  update user_notes un set entity_id = m.keep_id from _album_merge m
    where un.entity_type = 'album' and un.entity_id = m.drop_id;
  delete from metadata_matches mm using _album_merge m
    where mm.entity_type = 'album' and mm.entity_id = m.drop_id;
  delete from albums a using _album_merge m where a.id = m.drop_id;

  update albums set primary_artist_id = p_keep where primary_artist_id = p_drop;
  update tracks set primary_artist_id = p_keep where primary_artist_id = p_drop;

  insert into track_artists (track_id, artist_id, role, position, source)
    select track_id, p_keep, role, position, source from track_artists where artist_id = p_drop
    on conflict do nothing;
  delete from track_artists where artist_id = p_drop;

  insert into artist_genres (artist_id, genre_id, weight, source)
    select p_keep, genre_id, weight, source from artist_genres where artist_id = p_drop
    on conflict do nothing;
  delete from artist_genres where artist_id = p_drop;

  update artist_name_map set artist_id = p_keep where artist_id = p_drop;

  delete from library_items li using library_items k
    where li.entity_type = 'artist' and li.entity_id = p_drop
      and k.user_id = li.user_id and k.entity_type = 'artist' and k.entity_id = p_keep;
  update library_items set entity_id = p_keep where entity_type = 'artist' and entity_id = p_drop;
  delete from collection_items ci using collection_items k
    where ci.entity_type = 'artist' and ci.entity_id = p_drop
      and k.collection_id = ci.collection_id and k.entity_type = 'artist' and k.entity_id = p_keep;
  update collection_items set entity_id = p_keep where entity_type = 'artist' and entity_id = p_drop;
  delete from user_notes un using user_notes k
    where un.entity_type = 'artist' and un.entity_id = p_drop
      and k.user_id = un.user_id and k.entity_type = 'artist' and k.entity_id = p_keep;
  update user_notes set entity_id = p_keep where entity_type = 'artist' and entity_id = p_drop;

  delete from metadata_matches where entity_type = 'artist' and entity_id = p_drop;
  delete from artists where id = p_drop;
end;
$$;

-- ---------------------------------------------------------------------------
-- What to look up next, most-listened first.
-- p = {"kind": "artist"|"album"|"track", "limit": 50, "min_plays": 1}
-- Albums and tracks are only queued once their artist has an MBID: searching
-- inside a known artist is far more reliable than searching everything.
-- ---------------------------------------------------------------------------
create or replace function public.musicbrainz_queue(p jsonb)
returns jsonb
language plpgsql
stable
set search_path = public
as $$
declare
  v_kind      text    := p->>'kind';
  v_limit     integer := coalesce((p->>'limit')::int, 50);
  v_min_plays integer := coalesce((p->>'min_plays')::int, 1);
  v_out       jsonb;
begin
  if v_kind = 'artist' then
    with track_listening as (
      select e.track_id, sum(e.ms_played) as ms, count(*) as n,
             max(e.ms_played) filter (where e.reason_end = 'trackdone') as est_duration_ms
      from listening_events e group by e.track_id
    ), artist_listening as (
      select t.primary_artist_id as artist_id, sum(tl.ms) as ms, sum(tl.n) as n
      from track_listening tl join tracks t on t.id = tl.track_id
      group by t.primary_artist_id
    ), picked as (
      select a.id, a.name, al.ms
      from artists a join artist_listening al on al.artist_id = a.id
      where a.enrich_attempted_at is null and al.n >= v_min_plays
      order by al.ms desc
      limit v_limit
    )
    select coalesce(jsonb_agg(jsonb_build_object(
      'id', pk.id,
      'name', pk.name,
      'listening_ms', pk.ms,
      'manual_mbid', (select mm.external_id from metadata_matches mm
                      where mm.entity_type = 'artist' and mm.entity_id = pk.id
                        and mm.status = 'manual' order by mm.matched_at desc limit 1),
      'rejected', (select coalesce(jsonb_agg(mm.external_id), '[]') from metadata_matches mm
                   where mm.entity_type = 'artist' and mm.entity_id = pk.id and mm.status = 'rejected'),
      'top_tracks', (select coalesce(jsonb_agg(jsonb_build_object(
                        'id', x.id, 'title', x.title, 'version_type', x.version_type,
                        'est_duration_ms', x.est_duration_ms) order by x.ms desc), '[]')
                     from (select t.id, t.title, t.version_type, tl.ms, tl.est_duration_ms
                           from tracks t join track_listening tl on tl.track_id = t.id
                           where t.primary_artist_id = pk.id
                           order by tl.ms desc limit 2) x)
    ) order by pk.ms desc), '[]')
    into v_out from picked pk;

  elsif v_kind = 'track' then
    with track_listening as (
      select e.track_id, sum(e.ms_played) as ms, count(*) as n,
             max(e.ms_played) filter (where e.reason_end = 'trackdone') as est_duration_ms
      from listening_events e group by e.track_id
    )
    select coalesce(jsonb_agg(jsonb_build_object(
      'id', t.id, 'title', t.title, 'version_type', t.version_type,
      'est_duration_ms', tl.est_duration_ms, 'listening_ms', tl.ms,
      'artist_name', a.name, 'artist_mbid', a.mbid,
      'manual_mbid', (select mm.external_id from metadata_matches mm
                      where mm.entity_type = 'track' and mm.entity_id = t.id
                        and mm.status = 'manual' order by mm.matched_at desc limit 1),
      'rejected', (select coalesce(jsonb_agg(mm.external_id), '[]') from metadata_matches mm
                   where mm.entity_type = 'track' and mm.entity_id = t.id and mm.status = 'rejected')
    ) order by tl.ms desc), '[]')
    into v_out
    from (select t.* from tracks t
          join track_listening tl on tl.track_id = t.id
          join artists a on a.id = t.primary_artist_id
          where t.enrich_attempted_at is null and a.mbid is not null and tl.n >= v_min_plays
          order by tl.ms desc limit v_limit) t
    join track_listening tl on tl.track_id = t.id
    join artists a on a.id = t.primary_artist_id;

  elsif v_kind = 'album' then
    with album_listening as (
      select t.album_id, sum(e.ms_played) as ms, count(*) as n
      from listening_events e join tracks t on t.id = e.track_id
      where t.album_id is not null group by t.album_id
    )
    select coalesce(jsonb_agg(jsonb_build_object(
      'id', al.id, 'title', al.title, 'listening_ms', l.ms,
      'artist_name', a.name, 'artist_mbid', a.mbid,
      'manual_mbid', (select mm.external_id from metadata_matches mm
                      where mm.entity_type = 'album' and mm.entity_id = al.id
                        and mm.status = 'manual' order by mm.matched_at desc limit 1),
      'rejected', (select coalesce(jsonb_agg(mm.external_id), '[]') from metadata_matches mm
                   where mm.entity_type = 'album' and mm.entity_id = al.id and mm.status = 'rejected')
    ) order by l.ms desc), '[]')
    into v_out
    from (select al.* from albums al
          join album_listening l on l.album_id = al.id
          join artists a on a.id = al.primary_artist_id
          where al.enrich_attempted_at is null and a.mbid is not null and l.n >= v_min_plays
          order by l.ms desc limit v_limit) al
    join album_listening l on l.album_id = al.id
    join artists a on a.id = al.primary_artist_id;

  else
    raise exception 'unknown kind %', v_kind;
  end if;

  return v_out;
end;
$$;

-- ---------------------------------------------------------------------------
-- Record and apply enrichment decisions.
-- p = {"results": [{"kind", "id", "status", "mbid", "confidence",
--                    "fields": {...}, "candidates": [...]}]}
-- ---------------------------------------------------------------------------
create or replace function public.apply_musicbrainz_results(p jsonb)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  r          jsonb;
  v_kind     text;
  v_id       uuid;
  v_status   text;
  v_mbid     text;
  f          jsonb;
  v_locked   boolean;
  v_other    uuid;
  v_applied  integer := 0;
  v_recorded integer := 0;
  v_merged   integer := 0;
  v_skipped_locked integer := 0;
begin
  for r in select * from jsonb_array_elements(coalesce(p->'results', '[]')) loop
    v_kind   := r->>'kind';
    v_id     := (r->>'id')::uuid;
    v_status := r->>'status';
    v_mbid   := nullif(r->>'mbid', '');
    f        := coalesce(r->'fields', '{}');

    if v_kind not in ('artist', 'album', 'track') then
      raise exception 'unknown kind %', v_kind;
    end if;
    if v_status not in ('auto', 'suggested', 'unmatched', 'confirmed', 'manual') then
      raise exception 'status % cannot be submitted', v_status;
    end if;

    -- Entity may have been merged away by an earlier result in this chunk.
    if v_kind = 'artist' and not exists (select 1 from artists where id = v_id) then
      continue;
    end if;

    -- Locked = you confirmed or set a different MBID yourself.
    select exists (
      select 1 from metadata_matches
      where entity_type = v_kind and entity_id = v_id and provider = 'musicbrainz'
        and status in ('manual', 'confirmed') and external_id is distinct from v_mbid
    ) into v_locked;

    -- Record the decision.
    if v_mbid is null then
      delete from metadata_matches
        where entity_type = v_kind and entity_id = v_id and provider = 'musicbrainz' and status = 'unmatched';
    end if;
    insert into metadata_matches (entity_type, entity_id, provider, external_id, confidence, status, payload)
    values (v_kind, v_id, 'musicbrainz', v_mbid, (r->>'confidence')::real, v_status,
            jsonb_build_object('fields', f, 'candidates', coalesce(r->'candidates', '[]')))
    on conflict (entity_type, entity_id, provider, external_id) do update
      set confidence = excluded.confidence,
          payload    = excluded.payload,
          matched_at = now(),
          status = case
            when metadata_matches.status in ('manual', 'rejected') then metadata_matches.status
            when metadata_matches.status = 'confirmed' and excluded.status <> 'manual' then 'confirmed'
            else excluded.status end;
    v_recorded := v_recorded + 1;

    if v_kind = 'artist' then
      update artists set enrich_attempted_at = now() where id = v_id;
    elsif v_kind = 'album' then
      update albums set enrich_attempted_at = now() where id = v_id;
    else
      update tracks set enrich_attempted_at = now() where id = v_id;
    end if;

    if v_status not in ('auto', 'confirmed', 'manual') or v_mbid is null then
      continue;
    end if;
    if v_locked then
      v_skipped_locked := v_skipped_locked + 1;
      continue;
    end if;

    if v_kind = 'artist' then
      -- Another MUSE artist already has this MBID: they're the same artist.
      select id into v_other from artists where mbid = v_mbid::uuid and id <> v_id;
      if v_other is not null then
        perform merge_artists(v_other, v_id);
        v_id := v_other;
        v_merged := v_merged + 1;
      end if;

      update artists set
        mbid            = v_mbid::uuid,
        sort_name       = coalesce(f->>'sort_name', sort_name),
        country         = coalesce(f->>'country', country),
        mb_type         = coalesce(f->>'type', mb_type),
        disambiguation  = coalesce(f->>'disambiguation', disambiguation),
        metadata_source = 'musicbrainz',
        enriched_at     = now()
      where id = v_id;

      if jsonb_typeof(f->'genres') = 'array' then
        delete from artist_genres where artist_id = v_id and source = 'musicbrainz';
        insert into genres (name)
          select distinct g->>'name' from jsonb_array_elements(f->'genres') g
          on conflict (name) do nothing;
        insert into artist_genres (artist_id, genre_id, weight, source)
          select v_id, gn.id, greatest((g->>'count')::real, 1), 'musicbrainz'
          from jsonb_array_elements(f->'genres') g
          join genres gn on gn.name = g->>'name'
          on conflict (artist_id, genre_id) do update set weight = excluded.weight, source = excluded.source;
      end if;

    elsif v_kind = 'album' then
      update albums set
        mbid                   = v_mbid::uuid,
        release_date           = coalesce((f->>'release_date')::date, release_date),
        release_date_precision = coalesce(f->>'release_date_precision', release_date_precision),
        album_type             = coalesce(f->>'album_type', album_type),
        cover_url              = coalesce(f->>'cover_url', cover_url),
        metadata_source        = 'musicbrainz',
        enriched_at            = now()
      where id = v_id;

      -- The release's lyrics language becomes the language of its tracks,
      -- unless a track's language came from somewhere more specific.
      if f->>'language' is not null then
        update tracks set language = f->>'language', language_source = 'musicbrainz_release'
        where album_id = v_id
          and (language_source is null or language_source = 'musicbrainz_release');
      end if;

    else
      update tracks set
        mbid            = v_mbid::uuid,
        isrc            = coalesce(f->>'isrc', isrc),
        duration_ms     = coalesce((f->>'length_ms')::int, duration_ms),
        metadata_source = 'musicbrainz',
        enriched_at     = now()
      where id = v_id;
    end if;

    v_applied := v_applied + 1;
  end loop;

  return jsonb_build_object('recorded', v_recorded, 'applied', v_applied,
                            'artists_merged', v_merged, 'skipped_locked', v_skipped_locked);
end;
$$;

-- ---------------------------------------------------------------------------
-- Review: suggestions waiting for a decision, most-listened first.
-- ---------------------------------------------------------------------------
create or replace function public.musicbrainz_review(p jsonb)
returns jsonb
language sql
stable
set search_path = public
as $$
  select coalesce(jsonb_agg(x order by x.listening_ms desc nulls last), '[]')
  from (
    select mm.id as match_id, mm.entity_type, mm.entity_id, mm.external_id as mbid,
           round(mm.confidence::numeric, 2) as confidence,
           case mm.entity_type
             when 'artist' then (select name from artists where id = mm.entity_id)
             when 'album'  then (select al.title || ' — ' || a.name from albums al
                                 join artists a on a.id = al.primary_artist_id where al.id = mm.entity_id)
             else (select t.title || ' — ' || a.name from tracks t
                   join artists a on a.id = t.primary_artist_id where t.id = mm.entity_id)
           end as muse_name,
           mm.payload->'fields'->>'label' as candidate,
           case mm.entity_type
             when 'artist' then (select sum(e.ms_played) from listening_events e
                                 join tracks t on t.id = e.track_id where t.primary_artist_id = mm.entity_id)
             when 'album'  then (select sum(e.ms_played) from listening_events e
                                 join tracks t on t.id = e.track_id where t.album_id = mm.entity_id)
             else (select sum(e.ms_played) from listening_events e where e.track_id = mm.entity_id)
           end as listening_ms
    from metadata_matches mm
    where mm.provider = 'musicbrainz' and mm.status = 'suggested'
      and (p->>'kind' is null or mm.entity_type = p->>'kind')
    limit coalesce((p->>'limit')::int, 50)
  ) x
$$;

-- p = {"match_id": 123, "decision": "confirm"|"reject"}
create or replace function public.resolve_musicbrainz_match(p jsonb)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  m metadata_matches%rowtype;
begin
  select * into m from metadata_matches where id = (p->>'match_id')::bigint for update;
  if not found then
    raise exception 'match % not found', p->>'match_id';
  end if;

  if p->>'decision' = 'reject' then
    update metadata_matches set status = 'rejected' where id = m.id;
    return jsonb_build_object('rejected', m.id);
  elsif p->>'decision' = 'confirm' then
    -- Other suggestions for the same thing are implicitly wrong.
    update metadata_matches set status = 'rejected'
      where entity_type = m.entity_type and entity_id = m.entity_id and provider = m.provider
        and id <> m.id and status in ('suggested', 'auto');
    update metadata_matches set status = 'confirmed' where id = m.id;
    return apply_musicbrainz_results(jsonb_build_object('results', jsonb_build_array(jsonb_build_object(
      'kind', m.entity_type, 'id', m.entity_id, 'status', 'confirmed', 'mbid', m.external_id,
      'confidence', 1, 'fields', m.payload->'fields', 'candidates', m.payload->'candidates'))));
  else
    raise exception 'decision must be confirm or reject';
  end if;
end;
$$;

-- Set an MBID by hand. The next enrich run looks it up and fills in details.
-- p = {"kind": "artist"|"album"|"track", "id": uuid, "mbid": text}
create or replace function public.set_manual_musicbrainz_id(p jsonb)
returns void
language plpgsql
set search_path = public
as $$
declare
  v_kind text := p->>'kind';
  v_id   uuid := (p->>'id')::uuid;
begin
  if v_kind not in ('artist', 'album', 'track') then
    raise exception 'unknown kind %', v_kind;
  end if;
  perform (p->>'mbid')::uuid;  -- validates the format

  update metadata_matches set status = 'rejected'
    where entity_type = v_kind and entity_id = v_id and provider = 'musicbrainz'
      and status in ('manual', 'confirmed', 'auto', 'suggested') and external_id <> p->>'mbid';
  insert into metadata_matches (entity_type, entity_id, provider, external_id, confidence, status)
    values (v_kind, v_id, 'musicbrainz', p->>'mbid', 1, 'manual')
    on conflict (entity_type, entity_id, provider, external_id) do update set status = 'manual';

  if v_kind = 'artist' then
    update artists set enrich_attempted_at = null where id = v_id;
  elsif v_kind = 'album' then
    update albums set enrich_attempted_at = null where id = v_id;
  else
    update tracks set enrich_attempted_at = null where id = v_id;
  end if;
end;
$$;

-- Find a MUSE entity by name, for the set-mbid command.
-- p = {"kind": ..., "name": ...}
create or replace function public.find_entity(p jsonb)
returns jsonb
language sql
stable
set search_path = public
as $$
  select coalesce(jsonb_agg(x), '[]') from (
    select a.id, a.name as label from artists a
      where p->>'kind' = 'artist' and a.name_key = name_key(p->>'name')
    union all
    select al.id, al.title || ' — ' || a.name from albums al join artists a on a.id = al.primary_artist_id
      where p->>'kind' = 'album' and al.title_key = name_key(p->>'name')
    union all
    select t.id, t.title || ' — ' || a.name from tracks t join artists a on a.id = t.primary_artist_id
      where p->>'kind' = 'track' and name_key(t.title) = name_key(p->>'name')
    limit 20
  ) x
$$;

-- Put entities back in the queue (e.g. after improving the matcher).
-- p = {"kind": ..., "only_unmatched": true}
create or replace function public.requeue_musicbrainz(p jsonb)
returns integer
language plpgsql
set search_path = public
as $$
declare
  v_kind text := p->>'kind';
  v_only_unmatched boolean := coalesce((p->>'only_unmatched')::boolean, true);
  n integer;
begin
  if v_kind = 'artist' then
    update artists set enrich_attempted_at = null
      where enrich_attempted_at is not null and (not v_only_unmatched or mbid is null);
  elsif v_kind = 'album' then
    update albums set enrich_attempted_at = null
      where enrich_attempted_at is not null and (not v_only_unmatched or mbid is null);
  elsif v_kind = 'track' then
    update tracks set enrich_attempted_at = null
      where enrich_attempted_at is not null and (not v_only_unmatched or mbid is null);
  else
    raise exception 'unknown kind %', v_kind;
  end if;
  get diagnostics n = row_count;
  return n;
end;
$$;

-- How much of your listening now has each kind of metadata.
create or replace function public.enrichment_coverage(p jsonb)
returns jsonb
language sql
stable
set search_path = public
as $$
  with ev as (
    select e.ms_played, a.mbid is not null as artist_matched, a.country is not null as has_country,
           exists (select 1 from artist_genres g where g.artist_id = a.id) as has_genre,
           t.mbid is not null as track_matched, t.language is not null as has_language,
           al.mbid is not null as album_matched, al.release_date is not null as has_release_date
    from listening_events e
    join tracks t on t.id = e.track_id
    join artists a on a.id = t.primary_artist_id
    left join albums al on al.id = t.album_id
  )
  select jsonb_build_object(
    'artists_matched',  (select count(*) from artists where mbid is not null),
    'artists_total',    (select count(*) from artists),
    'albums_matched',   (select count(*) from albums where mbid is not null),
    'albums_total',     (select count(*) from albums),
    'tracks_matched',   (select count(*) from tracks where mbid is not null),
    'tracks_total',     (select count(*) from tracks),
    'suggestions_waiting', (select count(*) from metadata_matches where status = 'suggested'),
    'listening_share', jsonb_build_object(
      'artist_matched', round(100.0 * sum(ms_played) filter (where artist_matched) / nullif(sum(ms_played), 0), 1),
      'country',        round(100.0 * sum(ms_played) filter (where has_country)    / nullif(sum(ms_played), 0), 1),
      'genre',          round(100.0 * sum(ms_played) filter (where has_genre)      / nullif(sum(ms_played), 0), 1),
      'language',       round(100.0 * sum(ms_played) filter (where has_language)   / nullif(sum(ms_played), 0), 1),
      'release_date',   round(100.0 * sum(ms_played) filter (where has_release_date) / nullif(sum(ms_played), 0), 1),
      'track_matched',  round(100.0 * sum(ms_played) filter (where track_matched)  / nullif(sum(ms_played), 0), 1)
    ))
  from ev
$$;

revoke execute on function public.merge_artists(uuid, uuid)              from public, anon, authenticated;
revoke execute on function public.musicbrainz_queue(jsonb)               from public, anon, authenticated;
revoke execute on function public.apply_musicbrainz_results(jsonb)       from public, anon, authenticated;
revoke execute on function public.musicbrainz_review(jsonb)              from public, anon, authenticated;
revoke execute on function public.resolve_musicbrainz_match(jsonb)       from public, anon, authenticated;
revoke execute on function public.set_manual_musicbrainz_id(jsonb)       from public, anon, authenticated;
revoke execute on function public.find_entity(jsonb)                     from public, anon, authenticated;
revoke execute on function public.requeue_musicbrainz(jsonb)             from public, anon, authenticated;
revoke execute on function public.enrichment_coverage(jsonb)             from public, anon, authenticated;
