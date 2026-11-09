import Link from "next/link";

export function SiteHeader({ signOut = true }: { signOut?: boolean }) {
  return (
    <header className="site-header">
      <Link href="/timeline" className="wordmark">MUSE</Link>
      <nav aria-label="Main">
        <Link href="/timeline" aria-current="page">Timeline</Link>
        {signOut && (
          <form action="/auth/signout" method="post">
            <button type="submit" className="text-button">Sign out</button>
          </form>
        )}
      </nav>
    </header>
  );
}
