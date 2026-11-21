export function Header() {
  return (
    <header className="border-b-2 border-accent bg-background/90">
      <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-6">
        <div>
          <p className="font-mono text-[1.75rem] leading-none font-semibold tracking-tight">
            <span className="text-paper">git</span>
            <span className="text-accent">pro.be</span>
          </p>
          <p className="mt-2 max-w-xl text-sm text-muted sm:text-[15px]">
            Peek at every store&apos;s inventory before you buy — zero TCGplayer.
          </p>
        </div>
        <p className="text-xs tracking-wide text-muted">Saved in this browser. No account.</p>
      </div>
    </header>
  );
}
