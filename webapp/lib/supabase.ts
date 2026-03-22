import { createClient } from "@supabase/supabase-js";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";

// Returns null when env vars are not configured (e.g. during local dev without .env.local)
export const supabase = url && key ? createClient(url, key) : null;
