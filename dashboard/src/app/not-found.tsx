import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="bg-card border border-card-border rounded-xl p-6">
        <h2 className="text-xl font-semibold mb-2">Route not found</h2>
        <p className="text-sm text-muted mb-4">
          This dashboard route is not available in the current build.
        </p>
        <Link
          href="/"
          className="inline-flex px-4 py-2 rounded-lg bg-accent text-black text-sm font-medium hover:opacity-90"
        >
          Return to overview
        </Link>
      </div>
    </div>
  );
}
