"use client";

export default function Loading() {
  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="bg-card border border-card-border rounded-xl p-6">
        <h2 className="text-lg font-semibold mb-2">Loading dashboard</h2>
        <p className="text-sm text-muted">
          Preparing API-backed module data for this route.
        </p>
      </div>
    </div>
  );
}
