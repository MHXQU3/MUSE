-- MUSE core schema
-- Target: Supabase (Postgres 15+). Run in order with the other migrations.
--
-- Layout
--   Shared music metadata (same for every user): artists, albums, tracks,
--     track_artists, genres, artist_genres, metadata_matches
--   Per-user data (protected by row level security): profiles,
--     listening_events, library_items, collections, collection_items,
--     user_notes, import_batches
--   Import plumbing (server only): import_staging, artist_name_map
--
-- Principles
--   * Every play is stored as its own row. Stats are calculated from events.
--   * Library (what you saved) and history (what you played) are separate.
--   * Metadata records where it came from, so it can be corrected later.
--   * Imports are tracked in batches so one can be rolled back.

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Matching key for names coming from exports: lower case, trimmed, single
-- spaces. Accents are kept on purpose ("Béné" and "Bene" may differ).
create or replace function public.name_key(s text) returns text
language sql immutable parallel safe as $$
  select lower(btrim(regexp_replace(coalesce(s, ''), '\s+', ' ', 'g')))
$$;

create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end $$;

-- ---------------------------------------------------------------------------
-- Users
-- ---------------------------------------------------------------------------

create table public.profiles (
  id            uuid primary key references auth.users (id) on delete cascade,
  display_name  text,
  avatar_path   text,
  timezone      text not null default 'Europe/London',
  -- Plays shorter than this are kept but not counted as plays in stats.
  min_play_ms   integer not null default 30000 check (min_play_ms >= 0),
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Shared music metadata
-- ---------------------------------------------------------------------------

create table public.artists (
  id               uuid primary key default gen_random_uuid(),
  name             text not null,
  name_key         text not null generated always as (public.name_key(name)) stored,
  sort_name        text,
  country          char(2),             -- ISO 3166-1 alpha-2
  mbid             uuid unique,         -- MusicBrainz artist id
  spotify_id       text unique,
  metadata_source  text not null default 'spotify_export',
  enriched_at      timestamptz,
  created_at       timestamptz not null default now()
);
create index artists_name_key_idx on public.artists (name_key);

-- Maps a raw artist name from an export to an artist. Kept separate from
-- artists so two different artists with the same name can be split later
-- without breaking future imports.
create table public.artist_name_map (
  name_key   text primary key,
  artist_id  uuid not null references public.artists (id) on delete cascade
);

create table public.albums (
  id                      uuid primary key default gen_random_uuid(),
  title                   text not null,
  title_key               text not null generated always as (public.name_key(title)) stored,
  primary_artist_id       uuid not null references public.artists (id),
  release_date            date,
  release_date_precision  text check (release_date_precision in ('year', 'month', 'day')),
  album_type              text check (album_type in ('album', 'single', 'ep', 'compilation', 'other')),
  mbid                    uuid unique,  -- MusicBrainz release group id
  spotify_id              text unique,
  cover_path              text,
  metadata_source         text not null default 'spotify_export',
  enriched_at             timestamptz,
  created_at              timestamptz not null default now(),
  unique (primary_artist_id, title_key)
);

create table public.tracks (
  id                 uuid primary key default gen_random_uuid(),
  title              text not null,
  album_id           uuid references public.albums (id),
  primary_artist_id  uuid not null references public.artists (id),
  duration_ms        integer check (duration_ms > 0),
  spotify_uri        text unique,
  isrc               text,
  mbid               uuid,              -- MusicBrainz recording id
  language           char(3),           -- ISO 639-3, e.g. fra, spa, ara, eng
  language_source    text,
  version_type       text not null default 'original'
                     check (version_type in ('original', 'remix', 'remaster', 'live',
                                             'acoustic', 'sped_up', 'slowed', 'edit', 'other')),
  metadata_source    text not null default 'spotify_export',
  enriched_at        timestamptz,
  created_at         timestamptz not null default now()
);
create index tracks_album_idx on public.tracks (album_id);
create index tracks_artist_idx on public.tracks (primary_artist_id);
create index tracks_isrc_idx on public.tracks (isrc);

create table public.track_artists (
  track_id   uuid not null references public.tracks (id) on delete cascade,
  artist_id  uuid not null references public.artists (id) on delete cascade,
  role       text not null check (role in ('primary', 'featured', 'remixer', 'producer')),
  position   smallint not null default 0,
  source     text not null default 'spotify_export',
  primary key (track_id, artist_id, role)
);
create index track_artists_artist_idx on public.track_artists (artist_id);

create table public.genres (
  id         integer generated always as identity primary key,
  name       text not null unique,
  parent_id  integer references public.genres (id)
);

create table public.artist_genres (
  artist_id  uuid not null references public.artists (id) on delete cascade,
  genre_id   integer not null references public.genres (id) on delete cascade,
  weight     real not null default 1 check (weight > 0),
  source     text not null,
  primary key (artist_id, genre_id)
);

-- Every attempt to link a MUSE entity to an outside catalogue, with how sure
-- we are. Manual corrections live here too, so they survive re-enrichment.
create table public.metadata_matches (
  id           bigint generated always as identity primary key,
  entity_type  text not null check (entity_type in ('artist', 'album', 'track')),
  entity_id    uuid not null,
  provider     text not null check (provider in ('musicbrainz', 'spotify', 'cover_art_archive', 'manual')),
  external_id  text,
  confidence   real check (confidence between 0 and 1),
  status       text not null default 'auto' check (status in ('auto', 'confirmed', 'rejected', 'manual')),
  payload      jsonb,
  matched_at   timestamptz not null default now(),
  unique (entity_type, entity_id, provider, external_id)
);
create index metadata_matches_entity_idx on public.metadata_matches (entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- Imports
-- ---------------------------------------------------------------------------

create table public.import_batches (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.profiles (id) on delete cascade,
  source        text not null default 'spotify_extended',
  status        text not null default 'staging'
                check (status in ('staging', 'complete', 'failed', 'rolled_back')),
  file_names    text[] not null default '{}',
  stats         jsonb not null default '{}',
  error         text,
  created_at    timestamptz not null default now(),
  completed_at  timestamptz
);
create index import_batches_user_idx on public.import_batches (user_id, created_at desc);

-- Cleaned rows from the importer wait here until process_import_batch() runs.
-- Sensitive export fields (IP address, user agent, country, platform) are
-- removed by the importer and never reach the database.
create unlogged table public.import_staging (
  batch_id           uuid not null references public.import_batches (id) on delete cascade,
  row_num            integer not null,
  ended_at           timestamptz not null,
  ms_played          integer not null,
  track_name         text not null,
  artist_name        text not null,
  album_name         text,
  spotify_track_uri  text not null,
  reason_start       text,
  reason_end         text,
  shuffle            boolean,
  skipped            boolean,
  offline            boolean,
  incognito          boolean,
  version_type       text not null default 'original',
  featured_artists   text[] not null default '{}',
  primary key (batch_id, row_num)
);

-- ---------------------------------------------------------------------------
-- Per-user data
-- ---------------------------------------------------------------------------

create table public.listening_events (
  id               bigint generated always as identity primary key,
  user_id          uuid not null references public.profiles (id) on delete cascade,
  track_id         uuid not null references public.tracks (id),
  started_at       timestamptz not null,  -- estimated: ended_at minus ms_played
  ended_at         timestamptz not null,  -- Spotify's "ts" is when playback stopped
  ms_played        integer not null check (ms_played >= 0),
  skipped          boolean,
  shuffle          boolean,
  offline          boolean,
  incognito        boolean,
  reason_start     text,
  reason_end       text,
  source           text not null default 'spotify_extended',
  import_batch_id  uuid references public.import_batches (id) on delete set null,
  created_at       timestamptz not null default now(),
  -- The same track cannot stop at the same instant twice: this is what makes
  -- re-importing overlapping exports safe.
  unique (user_id, ended_at, track_id)
);
create index listening_events_user_time_idx  on public.listening_events (user_id, started_at);
create index listening_events_user_track_idx on public.listening_events (user_id, track_id, started_at);
create index listening_events_batch_idx      on public.listening_events (import_batch_id);

-- What the user has saved, independent of whether they've played it.
create table public.library_items (
  user_id      uuid not null references public.profiles (id) on delete cascade,
  entity_type  text not null check (entity_type in ('artist', 'album', 'track')),
  entity_id    uuid not null,
  added_at     timestamptz not null default now(),
  source       text not null default 'manual',
  primary key (user_id, entity_type, entity_id)
);

create table public.collections (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references public.profiles (id) on delete cascade,
  title        text not null,
  description  text,
  cover_path   text,
  notes        text,
  is_public    boolean not null default false,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index collections_user_idx on public.collections (user_id);
create trigger collections_touch before update on public.collections
  for each row execute function public.touch_updated_at();

create table public.collection_items (
  id             bigint generated always as identity primary key,
  collection_id  uuid not null references public.collections (id) on delete cascade,
  entity_type    text not null check (entity_type in ('artist', 'album', 'track')),
  entity_id      uuid not null,
  -- Fractional ordering: dropping an item between 1 and 2 gives it 1.5,
  -- so drag-and-drop never has to renumber the whole list.
  position       double precision not null,
  note           text,
  added_at       timestamptz not null default now(),
  unique (collection_id, entity_type, entity_id)
);
create index collection_items_order_idx on public.collection_items (collection_id, position);

-- Personal rating (1-10, i.e. half stars out of 5) and notes on anything.
create table public.user_notes (
  user_id      uuid not null references public.profiles (id) on delete cascade,
  entity_type  text not null check (entity_type in ('artist', 'album', 'track')),
  entity_id    uuid not null,
  rating       smallint check (rating between 1 and 10),
  note         text,
  updated_at   timestamptz not null default now(),
  primary key (user_id, entity_type, entity_id)
);
create trigger user_notes_touch before update on public.user_notes
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------

alter table public.profiles          enable row level security;
alter table public.artists           enable row level security;
alter table public.artist_name_map   enable row level security;
alter table public.albums            enable row level security;
alter table public.tracks            enable row level security;
alter table public.track_artists     enable row level security;
alter table public.genres            enable row level security;
alter table public.artist_genres     enable row level security;
alter table public.metadata_matches  enable row level security;
alter table public.import_batches    enable row level security;
alter table public.import_staging    enable row level security;  -- no policies: server only
alter table public.listening_events  enable row level security;
alter table public.library_items     enable row level security;
alter table public.collections       enable row level security;
alter table public.collection_items  enable row level security;
alter table public.user_notes        enable row level security;

-- Shared metadata: any signed-in user can read; only the server writes.
create policy "read metadata" on public.artists          for select to authenticated using (true);
create policy "read metadata" on public.albums           for select to authenticated using (true);
create policy "read metadata" on public.tracks           for select to authenticated using (true);
create policy "read metadata" on public.track_artists    for select to authenticated using (true);
create policy "read metadata" on public.genres           for select to authenticated using (true);
create policy "read metadata" on public.artist_genres    for select to authenticated using (true);
create policy "read metadata" on public.metadata_matches for select to authenticated using (true);

-- Own profile only.
create policy "own profile" on public.profiles for all to authenticated
  using (id = auth.uid()) with check (id = auth.uid());

-- History: users can read and delete their own plays; the importer inserts.
create policy "read own plays"   on public.listening_events for select to authenticated using (user_id = auth.uid());
create policy "delete own plays" on public.listening_events for delete to authenticated using (user_id = auth.uid());

create policy "read own imports" on public.import_batches for select to authenticated using (user_id = auth.uid());

create policy "own library" on public.library_items for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "own notes" on public.user_notes for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

create policy "own collections" on public.collections for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "public collections" on public.collections for select to anon, authenticated
  using (is_public);

create policy "own collection items" on public.collection_items for all to authenticated
  using (exists (select 1 from public.collections c
                 where c.id = collection_id and c.user_id = auth.uid()))
  with check (exists (select 1 from public.collections c
                      where c.id = collection_id and c.user_id = auth.uid()));
create policy "public collection items" on public.collection_items for select to anon, authenticated
  using (exists (select 1 from public.collections c
                 where c.id = collection_id and c.is_public));

-- ---------------------------------------------------------------------------
-- Create a profile automatically when someone signs up
-- ---------------------------------------------------------------------------

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, display_name)
  values (new.id, split_part(coalesce(new.email, ''), '@', 1))
  on conflict (id) do nothing;
  return new;
end $$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();
