# Vendored libraries

| File | Package | Version | License |
|---|---|---|---|
| `supabase.js` | [`@supabase/supabase-js`](https://github.com/supabase/supabase-js) (`dist/umd/supabase.js`) | 2.117.3 | MIT, Copyright (c) 2020 Supabase |

Committed so the planner loads no third-party script at runtime (the CSP only allows our own).
SHA-256: `d6a5c4414a5d4ce646d9c1de223aa7067d3ff664c15394ffeb7fcffc763354a3`.

To update: `npm pack @supabase/supabase-js@<version>`, unpack it, copy `package/dist/umd/supabase.js`
here, and update the version and hash above.
