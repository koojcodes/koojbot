# MaxTheTrader Dashboard

Next.js dashboard for operating and monitoring the trading bot.

## Run Locally

1. Install dependencies:
   ```bash
   npm install
   ```
2. Set API endpoint in `.env.local` if needed:
   ```bash
   NEXT_PUBLIC_API_URL=http://localhost:8000
   ```
3. Start the dashboard:
   ```bash
   npm run dev
   ```
4. Open [http://localhost:3000](http://localhost:3000).

## Current Structure

- `src/app/` - route pages and global layout.
- `src/components/` - reusable UI components.
- `src/config/navigation.ts` - single source of truth for navigation modules.
- `src/lib/api.ts` - typed API client for bot endpoints.

## Rebuild Foundation Status

The dashboard has been cleaned for the next interactive iteration:

- Navigation is centralized and grouped by module area.
- Root layout now uses a consistent dashboard shell.
- Home route is now a readiness workspace with quick links to module routes.
- Legacy create-next-app README content was removed.

## Next Build Steps

- Add realtime websocket state for key metrics (portfolio, positions, signals).
- Introduce widget composition (drag, resize, reorder) for personalized layouts.
- Add filters and persisted user preferences for module pages.
- Add route-level loading and error states for all async pages.
