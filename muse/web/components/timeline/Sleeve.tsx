"use client";
// An album cover. Uses the Cover Art Archive image when there is one;
// otherwise (or if it fails to load) a typeset sleeve in the album's
// language colour, so every album still has a face.

import { type CSSProperties, useState } from "react";
import { languageColour } from "@/lib/format";

export function Sleeve({ title, artist, coverUrl, language }:
  { title: string; artist: string; coverUrl: string | null; language: string | null }) {
  const [failed, setFailed] = useState(false);
  if (coverUrl && !failed) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img className="sleeve" src={coverUrl} alt={`Cover of ${title} by ${artist}`}
           loading="lazy" onError={() => setFailed(true)} />
    );
  }
  return (
    <div className="sleeve sleeve-typeset" style={{ "--sleeve-ink": languageColour(language) } as CSSProperties}
         role="img" aria-label={`${title} by ${artist}`}>
      <span className="sleeve-title">{title}</span>
      <span className="sleeve-artist">{artist}</span>
    </div>
  );
}
