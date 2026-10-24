import { apiFetch } from "@/lib/api";
import type { AssistantAnswer } from "@/lib/assistant-format";

export function askAssistant(question: string): Promise<AssistantAnswer> {
  return apiFetch<AssistantAnswer>("/assistant/ask", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
}
