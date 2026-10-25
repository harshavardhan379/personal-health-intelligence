"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import RequireAuth from "@/components/RequireAuth";
import { Async, Card } from "@/components/states";
import { useAuth } from "@/lib/auth";
import { formatTimestamp } from "@/lib/format";
import { useApi } from "@/lib/useApi";

// Mirrors services/api/app/api/v1/account.py
type Account = {
  id: string;
  email: string;
  created_at: string | null;
  documents: number;
  events: { confirmed: number; edited: number; pending: number; rejected: number };
  privacy: { llm_provider: string; ai_runs_locally: boolean };
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 py-2 text-sm">
      <dt className="text-slate-500">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

function SettingsInner() {
  const { logout } = useAuth();
  const router = useRouter();
  const account = useApi<Account>("/account", { fallback: "Could not load your account" });

  return (
    <div className="space-y-6">
      <h2 className="text-2xl font-semibold">Settings</h2>

      <Async state={account} loadingLabel="Loading your account...">
        {(a) => (
          <>
            <Card title="Account">
              <dl className="mt-2 divide-y divide-slate-200">
                <Row label="Email">{a.email}</Row>
                <Row label="Member since">{formatTimestamp(a.created_at)}</Row>
                <Row label="Account id">
                  <span className="font-mono text-xs">{a.id}</span>
                </Row>
              </dl>
              <button
                type="button"
                onClick={() => {
                  logout();
                  router.push("/login");
                }}
                className="mt-4 rounded border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50"
              >
                Log out
              </button>
            </Card>

            <Card title="What is stored about you" description="This is everything you would be deleting.">
              <dl className="mt-2 divide-y divide-slate-200">
                <Row label="Uploaded documents">
                  <Link href="/documents" className="text-teal-700 underline">
                    {a.documents}
                  </Link>
                </Row>
                <Row label="Confirmed facts (your timeline)">
                  <Link href="/timeline" className="text-teal-700 underline">
                    {a.events.confirmed + a.events.edited}
                  </Link>
                </Row>
                <Row label="Facts awaiting your review">
                  <Link href="/review" className="text-teal-700 underline">
                    {a.events.pending}
                  </Link>
                </Row>
                <Row label="Facts you rejected">{a.events.rejected}</Row>
              </dl>
            </Card>

            <Card title="Privacy" description="Where your questions and your records go.">
              {a.privacy.ai_runs_locally ? (
                <p className="mt-3 rounded-md bg-green-50 p-3 text-sm text-green-900">
                  The assistant&rsquo;s model runs on this machine ({a.privacy.llm_provider}). Your
                  questions and the parts of your records it reads are not sent to an outside service.
                </p>
              ) : (
                <p role="alert" className="mt-3 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
                  The assistant uses an outside service ({a.privacy.llm_provider}). To answer, it is
                  sent the questions you ask and the excerpts of your records it needs for that answer.
                </p>
              )}
              <ul className="mt-3 list-disc space-y-1 pl-5 text-sm text-slate-600">
                <li>Answers use only facts you have confirmed. Nothing pending or rejected is used.</li>
                <li>Your uploaded files are kept in a folder reserved for your account.</li>
                <li>This is a demo prototype for synthetic data. Do not upload real health records.</li>
              </ul>
            </Card>

            <Card
              title="Delete your data"
              description="Remove your documents, extracted facts and account."
            >
              <p className="mt-3 rounded-md border border-dashed border-slate-300 p-3 text-sm text-slate-700">
                <strong>Not available yet.</strong> Deleting individual documents (YGH-119) and
                deleting your account (YGH-121) are planned and not built, so there is deliberately no
                delete button here. Until they exist, upload synthetic data only.
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled
                  aria-disabled="true"
                  className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-400"
                >
                  Delete my documents (not available yet)
                </button>
                <button
                  type="button"
                  disabled
                  aria-disabled="true"
                  className="rounded border border-slate-300 px-4 py-2 text-sm text-slate-400"
                >
                  Delete my account (not available yet)
                </button>
              </div>
            </Card>
          </>
        )}
      </Async>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <RequireAuth>
      <SettingsInner />
    </RequireAuth>
  );
}
