import Link from "next/link";

export default function NotFound() {
  return (
    <section className="mx-auto max-w-lg rounded-lg border border-slate-200 bg-white p-6">
      <h2 className="text-xl font-semibold">Page not found</h2>
      <p className="mt-2 text-sm text-slate-600">
        There is nothing at this address. It may have been moved, or the link may be wrong.
      </p>
      <Link
        href="/"
        className="mt-4 inline-block rounded bg-teal-700 px-4 py-2 text-sm font-medium text-white hover:bg-teal-800"
      >
        Go to the dashboard
      </Link>
    </section>
  );
}
