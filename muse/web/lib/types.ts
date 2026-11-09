// Shapes returned by the timeline_overview() and timeline_period() SQL functions.

export type LanguageMs = Record<string, number>;

export interface OverviewMonth {
  month: string;        // "2024-10"
  ms: number;
  plays: number;
  artists: number;
  languages: LanguageMs;
  top_artist: string;
}

export interface Overview {
  timezone: string | null;
  first_play: string | null; // local timestamp "2023-01-01T21:42:09"
  last_play: string | null;
  months: OverviewMonth[];
}

export interface PeriodData {
  start: string;
  end: string;
  bucket: "hour" | "day" | "month";
  totals: { ms: number; plays: number; artists: number; tracks: number; new_artists: number };
  top_artists: { id: string; name: string; country: string | null; ms: number; plays: number;
                 language: string | null; is_new: boolean }[];
  top_tracks: { id: string; title: string; artist: string; language: string | null; ms: number; plays: number }[];
  top_albums: { id: string; title: string; artist: string; ms: number; cover_url: string | null;
                release_date: string | null; language: string | null }[];
  discoveries: { id: string; name: string; first_at: string; first_track: string; ms: number }[];
  buckets: { key: string; ms: number; plays: number }[];
  languages: LanguageMs;
  log: { at: string; title: string; artist: string; ms_played: number; skipped: boolean;
         counted: boolean; language: string | null }[] | null;
}
