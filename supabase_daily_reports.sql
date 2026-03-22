-- Run this in Supabase Dashboard → SQL Editor
-- Adds the daily_reports table for the Vercel cron report

CREATE TABLE IF NOT EXISTS daily_reports (
    id           BIGSERIAL PRIMARY KEY,
    date         DATE UNIQUE NOT NULL,
    report_md    TEXT NOT NULL,
    generated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE daily_reports ENABLE ROW LEVEL SECURITY;
CREATE POLICY "public read daily_reports" ON daily_reports FOR SELECT USING (true);

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports (date DESC);
