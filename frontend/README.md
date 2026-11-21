# gitpro.be workspace

Next.js App Router UI for importing a deck and splitting it across Card Kingdom and Mana Pool.

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. The app calls the FastAPI backend at `NEXT_PUBLIC_API_BASE_URL` (default `http://localhost:8000`). A repository-root `.env` fills that value when `frontend/.env.local` does not set it.

```bash
npm run typecheck
npm run build
```

The parsed deck and optimizer settings are stored in this browser's `localStorage`. There are no accounts.
