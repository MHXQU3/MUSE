"use client";

export default function TimelineError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <main className="timeline">
      <section className="empty" role="alert">
        <h1>Timeline</h1>
        <p>{error.message || "The timeline didn't load."}</p>
        <p>Check that the database migrations up to <code>0005_timeline_functions.sql</code> have been run,
          then try again.</p>
        <button type="button" className="button" onClick={reset}>Try again</button>
      </section>
    </main>
  );
}
