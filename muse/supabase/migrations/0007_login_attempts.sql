-- MUSE: sign-in attempts, so the code sign-in can lock out guessing.
-- Only the website's server (using the secret key) reads or writes this table.

create table public.login_attempts (
  id            bigint generated always as identity primary key,
  email         text not null,
  ok            boolean not null,
  attempted_at  timestamptz not null default now()
);
create index login_attempts_recent_idx on public.login_attempts (email, attempted_at desc);

alter table public.login_attempts enable row level security;  -- no policies: server only
revoke all on public.login_attempts from anon, authenticated;
