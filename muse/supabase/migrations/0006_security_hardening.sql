-- MUSE: fixes from Supabase's security advisor.
-- The sign-up trigger function should only ever run as a trigger, never be
-- callable through the API; and helper functions get a fixed search_path.
-- (artist_name_map and import_staging have RLS with no policies on purpose:
-- only the importer, using the database connection, may touch them.)

revoke execute on function public.handle_new_user() from public, anon, authenticated;
alter function public.name_key(text) set search_path = public;
alter function public.touch_updated_at() set search_path = public;
