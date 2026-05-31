/**
 * Tailwind-friendly className combiner. Identical to the shadcn/ui
 * helper of the same name — kept here so we don't import shadcn's
 * lib for one function.
 */

import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
