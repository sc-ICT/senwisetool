---
name: view-state-and-loaders
description: >-
  Guide and operational standard for managing asynchronous view states, loaders,
  data availability, and request-level UI shielding in SenWiseTool frontend.
  Enforces double-submit prevention, guaranteed loading state teardown,
  accessible feedback, and resilient error recovery.
---

# View State, Loaders & Data Availability Standard

This skill defines the mandatory patterns for handling asynchronous operations, loaders, optimistic updates, and UI shielding across SenWiseTool applications.

---

## 1. Core Principles

1. **Guaranteed Teardown (No Stuck Loaders)**:
   - Any state variable indicating in-flight work (`isLoading`, `isPending`, `isSubmitting`) **MUST** be cleared in a `finally` block or via deterministic state machine transitions.
   - An unhandled error, rejected promise, or aborted request must **NEVER** leave a screen in an unclickable or perpetually spinning state.

2. **Strict UI Shielding During Mutations**:
   - When a user submits a form or triggers a state mutation:
     - All relevant interactive elements (buttons, inputs, select fields) must be **disabled** (`disabled={isLoading}`).
     - Visual indicators must switch to loading state (spinner inside button, skeleton placeholders, or overlay).
     - Rapid double-clicks or duplicate submissions must be physically ignored by checking in-flight status before execution.

3. **Data Availability & Graceful Fallbacks**:
   - Never render blank screens or `undefined` crashes while data loads.
   - Use skeleton screens matching the exact layout of the target data to eliminate Cumulative Layout Shift (CLS).
   - Always pair error states with a retry button (`onRetry`) and explicit, user-friendly error banners.

---

## 2. Standard Pattern: `useAsyncAction`

All mutating actions (submit, update, delete, trigger) should follow this safe pattern:

```typescript
import { useState, useCallback } from "react";

export function useAsyncAction<TArgs extends unknown[], TResult>(
  action: (...args: TArgs) => Promise<TResult>,
  options?: {
    onSuccess?: (result: TResult) => void;
    onError?: (error: unknown) => void;
  }
) {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const execute = useCallback(
    async (...args: TArgs): Promise<TResult | undefined> => {
      // 1. Guard against duplicate calls while request is in-flight
      if (isLoading) return;

      setIsLoading(true);
      setError(null);

      try {
        const result = await action(...args);
        options?.onSuccess?.(result);
        return result;
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Une erreur inattendue est survenue.";
        setError(message);
        options?.onError?.(err);
      } finally {
        // 2. ALWAYS reset loading state even if action fails or throws
        setIsLoading(false);
      }
    },
    [action, isLoading, options]
  );

  return { execute, isLoading, error, setError };
}
```

---

## 3. Form & Action Shielding Checklist

When writing forms and data actions:
- [ ] Primary submit button shows spinner and has `disabled={isLoading}`.
- [ ] Form inputs have `disabled={isLoading}` during active submission.
- [ ] Secondary action buttons (cancel, reset) are disabled or guarded during flight.
- [ ] Error messages display directly next to affected fields or in an accessible banner.
- [ ] On completion, loaders deactivate cleanly and provide visual confirmation (toast, checkmark, or redirect).

---

## 4. Hierarchy of Loaders

1. **Button Loaders (`AsyncButton`)**:
   - Best for localized user-triggered actions (e.g. "Save", "Submit", "Delete").
   - Maintains button dimensions to avoid layout jumps while displaying an inline spinner.

2. **Skeleton Placeholders (`Skeleton`)**:
   - Best for initial page loads and content fetching (tables, cards, profile details).
   - Matches the geometric shape and contrast of loaded components in both Light and Dark modes.

3. **Global Screen Loaders**:
   - Used only for initial session checks (`isCheckingAuth`) or critical whole-app transitions.
   - Must feature SenWiseTool brand logo, discreet pulse, and status message.
