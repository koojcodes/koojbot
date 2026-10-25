"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="bg-card border border-accent-red/30 rounded-xl p-6">
        <h2 className="text-xl font-semibold mb-2">Dashboard error</h2>
        <p className="text-sm text-muted mb-4">
          {error.message || "An unexpected error interrupted this route."}
        </p>
        <button
          onClick={() => reset()}
          className="px-4 py-2 rounded-lg bg-accent text-black text-sm font-medium hover:opacity-90"
        >
          Retry
        </button>
      </div>
    </div>
  );
}
